"""Fallback fetcher: plain HTTPS through the SOCKS proxy (or direct), Chrome UA, cookie jar persisted."""
import json
import os
import re

import requests

from _common import ACCEPT_LANGUAGE, PROXY, TMP, UA, cookie_domain

JAR = os.path.join(TMP, "serp_cookies.json")


def _load_jar(s):
    if os.path.exists(JAR):
        try:
            for c in json.load(open(JAR)):
                s.cookies.set(c["name"], c["value"], domain=c.get("domain", cookie_domain()), path=c.get("path", "/"))
        except Exception:  # noqa: BLE001
            pass


def save_cookies_from_chrome(cookies):
    keep = [{"name": c["name"], "value": c["value"], "domain": c.get("domain", cookie_domain()), "path": c.get("path", "/")}
            for c in cookies if c["name"] in ("SOCS", "CONSENT", "NID", "AEC", "__Secure-ENID")]
    os.makedirs(TMP, exist_ok=True)
    json.dump(keep, open(JAR, "w"))


def fetch(url, use_proxy=True, timeout=40):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": ACCEPT_LANGUAGE, "Accept": "text/html,*/*;q=0.8"})
    _load_jar(s)
    proxies = {"http": PROXY, "https": PROXY} if use_proxy else None
    r = s.get(url, proxies=proxies, timeout=timeout, allow_redirects=True)
    html = r.content.decode("utf-8", "replace")
    if "consent.google" in r.url and "SOCS" not in s.cookies.get_dict():
        # submit the "Accept all" consent form
        m = re.search(r'<form[^>]+action="(https://consent\.google\.[a-z]+/save)"(.*?)</form>', html, re.S)
        if m:
            fields = dict(re.findall(r'name="([^"]+)"\s+value="([^"]*)"', m.group(2)))
            s.post(m.group(1), data=fields, proxies=proxies, timeout=timeout)
            r = s.get(url, proxies=proxies, timeout=timeout)
            html = r.content.decode("utf-8", "replace")
    try:
        json.dump([{"name": k, "value": v, "domain": cookie_domain(), "path": "/"} for k, v in s.cookies.get_dict().items()], open(JAR, "w"))
    except Exception:  # noqa: BLE001
        pass
    return r.status_code, r.url, html
