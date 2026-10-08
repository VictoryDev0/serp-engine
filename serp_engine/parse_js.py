"""JS executed inside the SERP tab (JS view). Returns a JSON object; Python normalises it into the row dict.

Organic results are read from <cite> elements (the visible breadcrumb URL), skipping ads, the AI
Overview and "People also ask". Since 2026 Google wraps result anchors in opaque redirect links, so
the cite text is the most stable signal for which host ranks where. Label regexes cover the English
and German Google UI; extend them for other locales.
"""
import json
import re

from _common import norm_host

EXTRACT_JS = r"""(() => {
  const txt = document.body.innerText || "";
  const blocked = location.href.includes('/sorry/') || !!document.querySelector('#captcha-form, form[action*="sorry"], iframe[src*="recaptcha"]')
                  || /ungewöhnlichen Datenverkehr|unusual traffic/i.test(txt);
  const consent = [...document.querySelectorAll('button')].some(b => /^(Alle akzeptieren|Accept all)$/.test(b.innerText.trim()));
  const inAds = el => !!el.closest('#tads, #tadsb');
  // AI Overview container: block containing the "AI Overview" heading (localized).
  let aio = null;
  for (const h of document.querySelectorAll('h1,h2,h3,div[role=heading],span')) {
    const t = (h.innerText || '').trim();
    if (/^(KI-Übersicht|Übersicht mit KI|AI Overview)$/i.test(t)) { aio = h.closest('div[data-hveid]') || h.closest('div[jscontroller]') || h.parentElement; break; }
  }
  const aioText = aio ? aio.innerText : '';
  const inAio = el => aio ? aio.contains(el) : false;
  const inPaa = el => !!el.closest('[data-initq], [jsname="N760b"]');
  const organic = [];
  const seen = new Set();
  for (const c of document.querySelectorAll('#rso cite, #search cite')) {
    if (inAds(c) || inAio(c) || inPaa(c)) continue;
    const t = (c.innerText || '').trim();
    if (!t) continue;
    const m = t.match(/^(https?:\/\/[^\s›]+|[a-z0-9.-]+\.[a-z]{2,}[^\s›]*)/i);
    if (!m) continue;
    let url = m[1]; if (!/^https?:/.test(url)) url = 'https://' + url;
    const path = t.split(' › ').slice(1).join('/');
    const blk = c.closest('div[data-hveid]') || c.parentElement;
    const h3 = blk ? blk.querySelector('h3') : null;
    const a = h3 ? h3.closest('a[href^="http"]') : null;
    const full = a ? a.href : (url + (path ? '/' + path.replace(/\s+/g,'') : ''));
    const key = full.replace(/\/$/, '');
    if (seen.has(key)) continue;
    seen.add(key);
    organic.push({url: full, title: h3 ? h3.innerText.trim() : '', cite: t});
  }
  const ads = [];
  for (const a of document.querySelectorAll('#tads a[href^="http"], #tadsb a[href^="http"]')) {
    try { const h = new URL(a.href).hostname; if (!/google\./.test(h) && !ads.includes(h)) ads.push(h); } catch (e) {}
  }
  const adsTop = !!document.querySelector('#tads');
  const gesp = /Gesponsert|Sponsored/.test(txt);
  const packEl = document.querySelector('[data-attrid^="kc:/local"], .rllt__details, a[data-cid], div[jscontroller][data-async-context*="lrd"]');
  const pack = !!packEl || /Weitere (Unternehmen|Orte|Geschäfte)|More (businesses|places)/.test(txt);
  let packText = '';
  if (packEl) { const pb = packEl.closest('div[data-hveid]') || packEl.parentElement; packText = pb ? pb.innerText : ''; }
  const aioLinks = [];
  if (aio) {
    for (const a of aio.querySelectorAll('a[href^="http"]')) { try { const u = new URL(a.href); if (!/google\.|youtube\.com/.test(u.hostname)) aioLinks.push(a.href); } catch (e) {} }
  }
  return {blocked, consent, organic, ads, adsTop, gesp, pack, packText: packText.slice(0, 3000), aio: !!aio, aioText: aioText.slice(0, 6000), aioLinks,
          title: document.title, url: location.href, textLen: txt.length};
})()"""

EXPAND_AIO_JS = r"""(() => {
  for (const b of document.querySelectorAll('div[role=button], button, span[role=button]')) {
    const t = (b.innerText || '').trim();
    if (/^(Mehr anzeigen|Alle anzeigen|Weitere Websites|Mehr|Show more|Show all)$/i.test(t)) { b.click(); return 'clicked:' + t; }
  }
  return 'none';
})()"""

CONSENT_JS = r"""(() => {
  const b = [...document.querySelectorAll('button')].find(b => /^(Alle akzeptieren|Accept all)$/.test(b.innerText.trim()));
  if (b) { b.click(); return 'clicked'; }
  return 'nobtn';
})()"""


def normalise(raw, client, page, per_page=10):
    """raw = EXTRACT_JS result. Returns dict with organic list (pos, host, url, title), flags, client match."""
    hosts = {norm_host(client["domain"])} | {norm_host(a) for a in client.get("aliases", [])}
    brand = re.compile(client.get("brand_regex", "$^"), re.I)
    organic = []
    for i, o in enumerate(raw.get("organic") or [], 1):
        organic.append({"pos": (page - 1) * per_page + i, "host": norm_host(o["url"]), "url": o["url"], "title": o.get("title", "")})
    hit = next((o for o in organic if o["host"] in hosts), None)
    aio_domains = sorted({norm_host(u) for u in raw.get("aioLinks") or []})
    return {
        "organic": organic, "hit": hit,
        "ads": [{"host": norm_host(h), "url": "", "title": ""} for h in raw.get("ads") or []],
        "ads_top": 1 if raw.get("adsTop") or raw.get("gesp") else 0, "ads_count": len(raw.get("ads") or []),
        "local_pack": 1 if raw.get("pack") else 0,
        "client_in_pack": 1 if raw.get("pack") and brand.search(raw.get("packText") or "") else 0,
        "ai_overview": 1 if raw.get("aio") else 0,
        "client_in_aio": 1 if raw.get("aio") and (bool(hosts & set(aio_domains)) or brand.search(raw.get("aioText") or "")) else 0,
        "aio_domains": aio_domains, "aio_links": [{"host": norm_host(u), "url": u, "title": ""} for u in raw.get("aioLinks") or []],
        "aio_text": raw.get("aioText") or "", "blocked": bool(raw.get("blocked")), "consent": bool(raw.get("consent")),
    }
