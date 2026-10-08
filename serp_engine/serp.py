"""Per-keyword SERP fetch + parse in the chosen mode; html dump; block detection."""
import gzip
import os
import time
import urllib.parse

import fetch_http
import parse_html
import parse_js
from _common import DUMP_DIR, SHOT_DIR, google_base, log, pace, today
from keywords import uule


def serp_url(client, kw, page):
    loc = client.get("location", {})
    q = {"q": kw, "hl": loc.get("hl", "en"), "gl": loc.get("gl", "us"), "pws": "0"}
    if loc.get("uule_name"):
        q["uule"] = uule(loc["uule_name"])
    if page > 1:
        q["start"] = (page - 1) * 10
    return google_base() + "/search?" + urllib.parse.urlencode(q, safe="+")


def dump(client, kw, page, html, day=None):
    d = os.path.join(DUMP_DIR, day or today(), client["id"])
    os.makedirs(d, exist_ok=True)
    slug = "".join(ch if ch.isalnum() else "-" for ch in kw)[:60]
    p = os.path.join(d, f"{slug}_p{page}.html.gz")
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(html)
    return p


def fetch_keyword(client, kwrow, mode, chrome=None, use_proxy=True, shot=False):
    """Returns (result dict or None if blocked, blocked_flag). Pages until the client is found or pages exhausted."""
    kw, pages = kwrow["keyword"], kwrow["pages"]
    merged = {"organic": [], "ads": [], "ads_top": 0, "ads_count": 0, "local_pack": 0, "client_in_pack": 0,
              "ai_overview": 0, "client_in_aio": 0, "aio_domains": [], "aio_links": [], "aio_text": ""}
    html_path, position, url, hit_page = None, None, None, None
    for page in range(1, pages + 1):
        u = serp_url(client, kw, page)
        if mode == "chrome":
            chrome.navigate(u, settle=5)
            chrome.wait_text_stable(max_wait=10, quiet=2)
            raw = chrome.eval(parse_js.EXTRACT_JS) or {}
            if raw.get("consent"):
                chrome.eval(parse_js.CONSENT_JS)
                time.sleep(6)
                raw = chrome.eval(parse_js.EXTRACT_JS) or {}
                try:
                    fetch_http.save_cookies_from_chrome(chrome.cookies())
                except Exception:  # noqa: BLE001
                    pass
            if raw.get("aio"):
                chrome.eval(parse_js.EXPAND_AIO_JS)
                time.sleep(1.5)
                raw = chrome.eval(parse_js.EXTRACT_JS) or {}
            res = parse_js.normalise(raw, client, page)
            html = chrome.html()
            if shot and page == 1:
                os.makedirs(SHOT_DIR, exist_ok=True)
                slug = "".join(ch if ch.isalnum() else "-" for ch in kw)[:60]
                chrome.screenshot(os.path.join(SHOT_DIR, f"{today()}_{slug}.png"))
        else:
            status, final, html = fetch_http.fetch(u, use_proxy=use_proxy)
            res = parse_html.parse(html, final, client, page)
            if status in (429, 503):
                res["blocked"] = True
        p = dump(client, kw, page, html)
        html_path = html_path or p
        if res["blocked"]:
            log(f"BLOCKED on '{kw}' p{page} ({mode})")
            return None, True
        if page == 1:
            for k in ("ads", "ads_top", "ads_count", "local_pack", "client_in_pack", "ai_overview", "client_in_aio",
                      "aio_domains", "aio_links", "aio_text"):
                merged[k] = res[k]
        merged["organic"].extend(res["organic"])
        if res["hit"]:
            position, url, hit_page = res["hit"]["pos"], res["hit"]["url"], page
            break
        if len(res["organic"]) == 0 and page == 1:
            merged["soft_block"] = True
        if page < pages:
            pace()
    merged.update({"position": position, "url": url, "page": hit_page, "window": pages * 10,
                   "top10": [{"pos": o["pos"], "host": o["host"], "url": o["url"], "title": o["title"][:120]} for o in merged["organic"][:10]],
                   "html_path": html_path})
    return merged, False
