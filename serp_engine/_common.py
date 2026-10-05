"""Shared paths, logging, HTTP session, proxy probe for the SERP engine.

SERP engine = scheduled Google rank tracking per client keyword list plus a weekly AI-visibility
probe (Google AI Overview / AI Mode, Perplexity and other search-grounded models). It fetches Google
through a residential SOCKS proxy with a real headful Chrome (primary) or plain HTTPS (fallback),
stores everything in SQLite and delivers an HTML report, an optional Google Sheet and ntfy pushes.
"""
import datetime as dt
import os
import random
import re
import subprocess
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

TMP = os.environ.get("SERP_DATA_DIR") or os.path.join(ROOT, ".data")
DB_PATH = os.path.join(TMP, "serp_engine.db")
LOG_PATH = os.path.join(TMP, "serp_engine.log")
HTML_DIR = os.path.join(TMP, "serp_engine_html")
DUMP_DIR = os.path.join(TMP, "serp_html")
SHOT_DIR = os.path.join(TMP, "serp_shots")
CLIENTS_DIR = os.environ.get("SERP_CLIENTS_DIR") or os.path.join(HERE, "clients")

UA = os.environ.get("SERP_USER_AGENT") or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
PROXY = os.environ.get("SERP_PROXY", "socks5h://127.0.0.1:1080")
PACE_MIN = float(os.environ.get("SERP_PACE_MIN", "25"))
PACE_MAX = float(os.environ.get("SERP_PACE_MAX", "40"))
GOOGLE_HOST = os.environ.get("SERP_GOOGLE_HOST", "www.google.com")
ACCEPT_LANGUAGE = os.environ.get("SERP_ACCEPT_LANGUAGE", "en-US,en;q=0.9")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": ACCEPT_LANGUAGE,
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})


def google_base():
    return f"https://{GOOGLE_HOST}"


def cookie_domain():
    """'.google.de' for www.google.de etc."""
    i = GOOGLE_HOST.find("google.")
    return "." + GOOGLE_HOST[i:] if i >= 0 else ".google.com"


def log(msg):
    os.makedirs(TMP, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def pace():
    time.sleep(random.uniform(PACE_MIN, PACE_MAX))


def proxy_ip(timeout=25):
    """Exit IP through the SOCKS proxy, or None when the tunnel is down."""
    host = PROXY.split("://", 1)[-1]
    try:
        out = subprocess.run(["curl", "-s", "-m", str(timeout), "--socks5-hostname", host, "https://api.ipify.org"],
                             capture_output=True, text=True, timeout=timeout + 5).stdout.strip()
        return out if re.match(r"^\d+\.\d+\.\d+\.\d+$", out) else None
    except Exception:  # noqa: BLE001
        return None


def norm_host(url_or_host):
    h = (url_or_host or "").strip().lower()
    h = re.sub(r"^https?://", "", h).split("/")[0].split("?")[0]
    h = re.sub(r"^(www|m|amp)\.", "", h)
    return h.split(" ")[0].strip(" ›")


def today():
    return dt.date.today().isoformat()


def now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
