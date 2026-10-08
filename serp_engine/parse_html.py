"""Parser for the HTML (non-JS) Google view. Organic order = h3 anchors in document order."""
import html as _html
import re
from urllib.parse import parse_qs, unquote, urlparse

from _common import norm_host


def parse(html, url, client, page, per_page=10):
    blocked = "/sorry/" in url or 'id="captcha-form"' in html or "ungewöhnlichen Datenverkehr" in html or "unusual traffic" in html
    hosts = {norm_host(client["domain"])} | {norm_host(a) for a in client.get("aliases", [])}
    body = html
    for tag in ("tads", "tadsb"):
        body = re.sub(r'<div[^>]+id="%s".*?(?=<div[^>]+id="(?:res|search|rso)")' % tag, "", body, flags=re.S)
    organic, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(?:(?!</a>).)*?<h3[^>]*>(.*?)</h3>', body, re.S):
        href = _html.unescape(m.group(1))
        if href.startswith("/url?"):
            href = parse_qs(urlparse(href).query).get("q", [""])[0]
        href = unquote(href)
        if not href.startswith("http") or "google." in urlparse(href).netloc:
            continue
        key = href.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        organic.append({"pos": (page - 1) * per_page + len(organic) + 1, "host": norm_host(href), "url": href,
                        "title": " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())})
    hit = next((o for o in organic if o["host"] in hosts), None)
    return {"organic": organic, "hit": hit, "ads": [], "ads_top": 1 if re.search(r'id="tads"|Gesponsert|Anzeige|Sponsored', html) else 0,
            "ads_count": None, "local_pack": 1 if re.search(r"rllt__details|Weitere Unternehmen|More businesses", html) else 0,
            "client_in_pack": None, "ai_overview": None, "client_in_aio": None, "aio_domains": [], "aio_links": [],
            "aio_text": "", "blocked": blocked, "consent": "consent.google" in url}
