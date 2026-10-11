"""Minimal Gemini caller (OpenAI-compatible endpoint) with model chain + key rotation + backoff.

Free-tier quirks: older model names can 404 for new projects and the -latest aliases sometimes
return 503, so the caller walks a model chain and rotates keys.
"""
import json
import os
import re
import time

import requests

URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def _models():
    return [m.strip() for m in os.environ.get(
        "SERP_LLM_MODELS", "gemini-flash-lite-latest,gemini-3.6-flash,gemini-flash-latest").split(",") if m.strip()]


def _keys():
    # Read lazily: _common loads .env, and this module may be imported before it.
    return [k for k in (os.environ.get("GEMINI_API_KEY_FREE"), os.environ.get("GEMINI_API_KEY")) if k]


def call(system, user, max_tokens=2000, temperature=0.2):
    MODELS, KEYS = _models(), _keys()
    if not KEYS:
        raise RuntimeError("no GEMINI_API_KEY_FREE / GEMINI_API_KEY in env")
    last = None
    for model in MODELS:
        for key in KEYS:
            for attempt in range(2):
                try:
                    r = requests.post(URL, headers={"Authorization": f"Bearer {key}"}, timeout=120, json={
                        "model": model, "temperature": temperature, "max_tokens": max_tokens,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"]
                    last = f"{model}: HTTP {r.status_code} {r.text[:120]}"
                    if r.status_code == 429:
                        time.sleep(20 * (attempt + 1))
                        continue
                    if r.status_code in (404, 400):
                        break  # model not available for this key -> next key/model
                    time.sleep(5)
                except requests.RequestException as e:
                    last = f"{model}: {type(e).__name__}"
                    time.sleep(5)
    raise RuntimeError(f"LLM failed: {last}")


def call_json(system, user, **kw):
    txt = call(system, user, **kw)
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    body = m.group(1) if m else txt
    start = min([i for i in (body.find("["), body.find("{")) if i >= 0] or [0])
    body = body[start:]
    try:
        return json.loads(body)
    except Exception:
        # trim trailing junk after the last closing bracket
        end = max(body.rfind("]"), body.rfind("}"))
        return json.loads(body[: end + 1])


# ----------------------------------------------------------------------------- OpenRouter (search-grounded models)
def openrouter(model, messages, extra=None, timeout=120):
    """Returns (content, citations[list of urls], usage dict). Raises on failure."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("no OPENROUTER_API_KEY")
    # usage.include makes OpenRouter return the real charged cost per call, which is
    # what the weekly budget guard meters against. Without it the cost is unknowable
    # until the account page updates, i.e. far too late to stop a run.
    body = {"model": model, "messages": messages, "usage": {"include": True}}
    if extra:
        body.update(extra)
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", timeout=timeout,
                      headers={"Authorization": f"Bearer {key}", "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://example.com"),
                               "X-Title": "SERP engine"}, json=body)
    if r.status_code != 200:
        raise RuntimeError(f"openrouter {model}: HTTP {r.status_code} {r.text[:160]}")
    d = r.json()
    msg = d["choices"][0]["message"]
    cites = list(d.get("citations") or [])
    for a in msg.get("annotations") or []:
        u = (a.get("url_citation") or {}).get("url")
        if u:
            cites.append(u)
    return msg.get("content", ""), cites, d.get("usage", {})
