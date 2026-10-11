"""Ad-hoc Google search through the SERP engine's own Chrome, for agents that need web results.

Google Custom Search is closed to new customers, so other agents search Google here: same headful Chrome,
same proxy exit, same pacing and block pause as the rank tracker. Results carry title, url and snippet as JSON.

Wrap it in flock so it never collides with a scheduled rank run on the same Chrome profile:
    flock .data/chrome.lock python serp_engine/search.py --days 14 --pages 1 "q1" "q2" ...
    ... --file queries.txt   (one query per line)
Prints one JSON object: {"results": {query: [{pos,title,url,resolved,host,cite,snippet}]}, "blocked": bool, "paused_until": ...}
"""
import argparse
import json
import random
import re
import sys
import time
import urllib.parse

import requests

import blockguard
import parse_js
import store
from _common import PROXY, google_base, log, norm_host, pace

MAX_QUERIES = 40  # one run stays under ~30 minutes at 25-40 s per page
# Five unpaced debug searches in three minutes were enough to get the proxy exit IP a captcha. Never loop
# searches outside this script; it paces, and a block sets the engine-wide pause so the rank tracker backs off too.

GRAB_JS = r"""(() => {
  const out = [], seen = new Set();
  for (const h of document.querySelectorAll('#search a h3, #rso a h3')) {
    const a = h.closest('a'); if (!a || !a.href) continue;
    // since 09/2026 href is an opaque /goto?url=<token>; Python resolves it (resolve())
    const href = a.href;
    if (seen.has(href) || (/^https?:\/\/[^/]*google\./.test(href) && !href.includes('/goto?') && !href.includes('/url?'))) continue;
    seen.add(href);
    const box = a.closest('div.g, div[data-hveid], div.MjjYud') || a.parentElement;
    let snip = '';
    const s = box && box.querySelector('div[data-sncf], div.VwiC3b, span.aCOpRe, div[style*="-webkit-line-clamp"]');
    if (s) snip = s.innerText; else if (box) snip = box.innerText.replace(h.innerText, '');
    const cite = box && box.querySelector('cite');
    out.push({title: h.innerText, url: href, cite: cite ? cite.innerText : '', snippet: snip.trim().slice(0, 400)});
  }
  const t = document.body ? document.body.innerText.slice(0, 3000) : '';
  return {organic: out, blocked: location.href.includes('/sorry/') || /unusual traffic|ungewöhnlichen Datenverkehr/i.test(t)};
})()"""


def resolve(chrome, href, session):
    """Google's /goto?url=<token> hides the target. Ask Google for the redirect without rendering; fall back to
    letting Chrome follow it. Returns the target URL or None."""
    if "/url?" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        return (q.get("q") or q.get("url") or [None])[0]
    if "/goto?" not in href:
        return href
    try:
        r = session.get(href, allow_redirects=False, timeout=20)
        loc = r.headers.get("location")
        if loc and "google." not in urllib.parse.urlparse(loc).netloc:
            return loc
        m = re.search(r'(?:URL=|href=")(https?://[^"\'<>]+)', r.text)
        if m and "google." not in urllib.parse.urlparse(m.group(1)).netloc:
            return m.group(1).replace("&amp;", "&")
    except Exception as e:  # noqa: BLE001
        log(f"job_search resolve via http failed: {type(e).__name__}")
    try:
        chrome.navigate(href, settle=3)
        u = chrome.eval("location.href") or ""
        return u if "google." not in urllib.parse.urlparse(u).netloc else None
    except Exception:  # noqa: BLE001
        return None


def url_for(q, days, page, hl, gl="us"):
    p = {"q": q, "hl": hl, "gl": gl, "pws": "0", "num": "10"}
    if days:
        p["tbs"] = f"qdr:d{days}" if days < 7 else f"qdr:w{max(1, round(days / 7))}"
    if page > 1:
        p["start"] = str((page - 1) * 10)
    return google_base() + "/search?" + urllib.parse.urlencode(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--days", type=int, default=14, help="0 = no date filter")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--hl", default="en")
    ap.add_argument("--gl", default="us")
    ap.add_argument("--no-resolve", dest="resolve", action="store_false", help="keep Google's /goto links")
    ap.add_argument("--resolve-only", default="", help="regex on cite+title; resolve only matching hits (fewer Google calls)")
    ap.add_argument("--force", action="store_true", help="run even while the engine is paused")
    ap.add_argument("--block-pause-hours", type=float, default=12)
    a = ap.parse_args()
    qs = list(a.queries)
    if a.file:
        qs += [ln.strip() for ln in open(a.file, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    qs = qs[:MAX_QUERIES]
    conn = store.connect()
    if not a.force and (until := blockguard.paused(conn)):
        print(json.dumps({"results": {}, "blocked": True, "paused_until": until}))
        return
    from chrome_cdp import Chrome
    chrome = Chrome(proxy=PROXY.replace("socks5h://", "socks5://")).start()
    results, blocked, session = {}, False, None
    try:
        for i, q in enumerate(qs):
            rows = []
            for page in range(1, a.pages + 1):
                chrome.navigate(url_for(q, a.days, page, a.hl, a.gl), settle=5)
                chrome.wait_text_stable(max_wait=10, quiet=2)
                raw = chrome.eval(GRAB_JS) or {}
                if (chrome.eval(parse_js.EXTRACT_JS) or {}).get("consent"):
                    chrome.eval(parse_js.CONSENT_JS)
                    time.sleep(6)
                    raw = chrome.eval(GRAB_JS) or {}
                if raw.get("blocked"):
                    blocked = True
                    blockguard.pause(conn, a.block_pause_hours, f"job_search blocked on '{q}'")
                    break
                if session is None:
                    session = requests.Session()
                    session.headers.update({"User-Agent": chrome.eval("navigator.userAgent") or "Mozilla/5.0",
                                            "Referer": google_base() + "/"})
                    session.proxies = {"http": PROXY, "https": PROXY}
                for c in chrome.cookies():
                    session.cookies.set(c["name"], c["value"], domain=c.get("domain"))
                want = re.compile(a.resolve_only, re.I) if a.resolve_only else None
                for o in raw.get("organic") or []:
                    u = None
                    if a.resolve and (not want or want.search(f"{o.get('cite', '')} {o['title']}")):
                        u = resolve(chrome, o["url"], session)
                        time.sleep(random.uniform(0.8, 2.0))
                    rows.append({"pos": len(rows) + 1, "title": o["title"], "url": u or o["url"], "resolved": bool(u),
                                 "host": norm_host(u) if u else "", "cite": o.get("cite", ""), "snippet": o["snippet"]})
                if page < a.pages:
                    pace()
            results[q] = rows
            log(f"job_search '{q}': {len(rows)} results")
            if blocked:
                break
            if i < len(qs) - 1:
                pace()
    finally:
        chrome.stop()
    print(json.dumps({"results": results, "blocked": blocked, "paused_until": blockguard.paused(conn)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
