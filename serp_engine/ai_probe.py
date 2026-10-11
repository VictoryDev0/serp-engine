"""Weekly AI-visibility probe: Google AI Mode (Chrome), search-grounded models via OpenRouter, Gemini grounded.

Each answer is reduced to structured fields (which businesses are named, in which order, whether the
client is cited and how) by a cheap LLM extraction call.
"""
import json
import os
import re
import time
import urllib.parse

import budget
import llm
import store
from _common import google_base, log, norm_host

EXTRACT_SYSTEM = """You receive the answer of an AI search surface to a question about {topic}, plus the
list of linked domains. The client is "{label}" (domain {domain}). Reply with JSON ONLY:
{{"businesses":[{{"order":1,"name":"...","domain":"... or null","reason":"1 sentence"}}],
 "client_cited": true|false, "client_how": "name|link|both|none",
 "client_claims": ["every statement about the client, verbatim"], "client_accuracy": "correct|partial|wrong|n/a",
 "answer_type": "list|single recommendation|guide without providers"}}
Order = order of mention in the text. Do not invent providers that are not in the text."""

# The OpenAI search-preview models were withdrawn from OpenRouter; every call 404'd for
# weeks while the job still reported success. Both surfaces default to Perplexity's sonar
# family, which still has live search. Override per surface via env.
MODELS = {"perplexity": os.environ.get("SERP_PPLX_MODEL", "perplexity/sonar"),
          "openai_search": os.environ.get("SERP_OAI_MODEL", "perplexity/sonar-pro")}


def probe_gemini_grounded(conn, client, run_date, prompts):
    """Gemini with Google-Search grounding (free tier) = the 'Gemini app' answer surface; citations from groundingMetadata."""
    import requests
    keys = [k for k in (os.environ.get("GEMINI_API_KEY_FREE"), os.environ.get("GEMINI_API_KEY")) if k]
    models = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-flash-lite-latest"]
    n = 0
    for p in prompts:
        text, links, model_used = None, [], None
        for model in models:
            for key in keys:
                try:
                    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                      params={"key": key}, timeout=90,
                                      json={"contents": [{"parts": [{"text": p}]}], "tools": [{"google_search": {}}]})
                    if r.status_code != 200:
                        continue
                    d = r.json()
                    cand = (d.get("candidates") or [{}])[0]
                    text = "".join(pt.get("text", "") for pt in cand.get("content", {}).get("parts", []))
                    gm = cand.get("groundingMetadata") or {}
                    links = [ch.get("web", {}).get("uri") for ch in gm.get("groundingChunks", []) if ch.get("web", {}).get("uri")]
                    titles = [ch.get("web", {}).get("title") for ch in gm.get("groundingChunks", [])]
                    model_used = model
                    break
                except Exception:  # noqa: BLE001
                    continue
            if text:
                break
        if not text:
            log(f"gemini_grounded '{p[:40]}': no answer")
            continue
        # grounding URIs are redirectors; the chunk title carries the domain
        domains = []
        for t in titles:
            h = norm_host(t or "")
            if h and "." in h and h not in domains:
                domains.append(h)
        for m in re.finditer(r"https?://[^\s)\]]+", text):
            h = norm_host(m.group(0))
            if h and h not in domains:
                domains.append(h)
        res = _extract(client, "gemini_grounded", p, text, domains)
        store.save_probe(conn, client["id"], run_date, "gemini_grounded", p, model_used, res, text, {"links": links, "titles": titles})
        n += 1
        time.sleep(3)
    log(f"gemini_grounded: {n}/{len(prompts)} prompts")
    return n


def _extract(client, surface, prompt, text, domains):
    sysm = EXTRACT_SYSTEM.format(topic=client.get("topic", "local service providers"), label=client["label"], domain=client["domain"])
    try:
        d = llm.call_json(sysm, json.dumps({"prompt": prompt, "surface": surface, "text": text[:6000], "linked_domains": domains}, ensure_ascii=False))
        if isinstance(d, list):
            d = d[0] if d else {}
    except Exception as e:  # noqa: BLE001
        log(f"extract failed: {type(e).__name__}")
        d = {}
    hosts = {norm_host(client["domain"])} | {norm_host(a) for a in client.get("aliases", [])}
    brand = re.compile(client.get("brand_regex", "$^"), re.I)
    we = bool(hosts & set(domains)) or bool(d.get("client_cited")) or bool(brand.search(text))
    linked = next((u for u in d.get("_links", []) if norm_host(u) in hosts), None)
    return {"cited_domains": domains, "cited_names": [b.get("name") for b in d.get("businesses", []) if isinstance(b, dict)],
            "we_cited": we, "linked_page": linked, "client_how": d.get("client_how"), "client_accuracy": d.get("client_accuracy"),
            "answer_type": d.get("answer_type"), "claims": d.get("client_claims", [])}


def probe_openrouter(conn, client, run_date, surface, prompts):
    model = MODELS[surface]
    n = 0
    for p in prompts:
        if not budget.affordable(conn):
            log(f"{surface}: weekly budget {budget.limit():.2f} USD used up "
                f"({budget.spent(conn):.4f}), {len(prompts) - n} prompts skipped")
            break
        extra = {}
        if surface == "openai_search":
            loc = client.get("location", {})
            approx = {k: loc[k] for k in ("country", "city") if loc.get(k)}
            if approx:
                extra = {"web_search_options": {"user_location": {"type": "approximate", "approximate": approx}}}
        try:
            text, cites, usage = llm.openrouter(model, [{"role": "user", "content": p}], extra=extra)
        except Exception as e:  # noqa: BLE001
            log(f"{surface} '{p[:40]}': {e}")
            continue
        budget.charge(conn, usage)
        domains = []
        for u in cites:
            h = norm_host(u)
            if h and h not in domains:
                domains.append(h)
        for m in re.finditer(r"https?://[^\s)\]]+", text):
            h = norm_host(m.group(0))
            if h and h not in domains:
                domains.append(h)
        res = _extract(client, surface, p, text, domains)
        hosts = {norm_host(client["domain"])} | {norm_host(a) for a in client.get("aliases", [])}
        res["linked_page"] = next((u for u in cites if norm_host(u) in hosts), None)
        store.save_probe(conn, client["id"], run_date, surface, p, model, res, text, {"citations": cites, "usage": usage})
        n += 1
        time.sleep(2)
    log(f"{surface}: {n}/{len(prompts)} prompts, "
        f"{budget.spent(conn):.4f}/{budget.limit():.2f} USD this week")
    return n


def probe_google_ai_mode(conn, client, run_date, prompts, chrome):
    n = 0
    for p in prompts:
        loc = client.get("location", {})
        u = google_base() + "/search?" + urllib.parse.urlencode({"udm": "50", "q": p, "hl": loc.get("hl", "en"), "gl": loc.get("gl", "us")})
        chrome.navigate(u, settle=8)
        chrome.wait_text_stable(max_wait=30, quiet=3)
        info = chrome.eval(r"""(()=>{const links=[...document.querySelectorAll('a[href^="http"]')].map(a=>a.href).filter(h=>!/google\.|youtube\.com/.test(h));
            return {text: document.body.innerText, links: [...new Set(links)].slice(0,60), blocked: location.href.includes('/sorry/')}})()""") or {}
        if info.get("blocked"):
            log("AI mode blocked")
            import blockguard
            blockguard.pause(conn, 12, "google_ai_mode captcha")
            return n
        text = info.get("text", "")
        domains = []
        for u2 in info.get("links", []):
            h = norm_host(u2)
            if h and h not in domains:
                domains.append(h)
        res = _extract(client, "google_ai_mode", p, text, domains)
        hosts = {norm_host(client["domain"])} | {norm_host(a) for a in client.get("aliases", [])}
        res["linked_page"] = next((l for l in info.get("links", []) if norm_host(l) in hosts), None)
        store.save_probe(conn, client["id"], run_date, "google_ai_mode", p, "google-ai-mode", res, text[:20000], {"links": info.get("links", [])})
        n += 1
        time.sleep(20)
    log(f"google_ai_mode: {n}/{len(prompts)} prompts")
    return n


def import_baseline(conn, client, path, run_date):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    n = 0
    for prompt, v in d.items():
        res = {"cited_domains": v.get("cited_domains", []), "cited_names": [], "we_cited": v.get("we_cited"),
               "linked_page": None, "client_how": None, "client_accuracy": None, "answer_type": None}
        store.save_probe(conn, client["id"], run_date, "google_ai_mode", prompt, "baseline-manual", res, v.get("text", ""), v)
        n += 1
    log(f"baseline imported: {n} probes as {run_date}")
