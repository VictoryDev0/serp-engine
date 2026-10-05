"""Client configs and today's keyword set (cadence: daily always; weekly on Monday; monthly on the 1st).

A client config may also expand place-based keywords: `place_patterns` such as "plumber {place}"
are combined with the entries of `<client id>.places.json` whose `wave` is listed in
`place_expand.waves`. Each wave gets its own group (`place_w<N>`) and cadence.
"""
import datetime as dt
import glob
import json
import os
import random

from _common import CLIENTS_DIR

BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def load_clients(only=None):
    out = []
    for p in sorted(glob.glob(os.path.join(CLIENTS_DIR, "*.json"))):
        if p.endswith(".places.json"):
            continue
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        if only and c["id"] != only:
            continue
        out.append(c)
    return out


def uule(name):
    """Google's canonical-location parameter: w+CAIQICI<len char><base64 name without padding>."""
    import base64
    b = base64.b64encode(name.encode()).decode().rstrip("=")
    return "w+CAIQICI" + BASE64[len(name)] + b


def all_keywords(client):
    kws = []
    for k in client["keywords"]:
        kws.append({"keyword": k["keyword"], "grp": k["grp"], "cadence": k.get("cadence", "daily"),
                    "pages": client["groups"].get(k["grp"], {}).get("pages", 2)})
    have = {k["keyword"] for k in kws}
    expand = client.get("place_expand", {})
    waves = expand.get("waves") or []
    if waves:
        p = os.path.join(CLIENTS_DIR, f"{client['id']}.places.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                places = json.load(f)
            for o in places:
                if o.get("wave") not in waves:
                    continue
                for pat in client.get("place_patterns", []):
                    kw = pat.format(place=o["name"].lower())
                    if kw in have:
                        continue
                    have.add(kw)
                    grp = f"place_w{o['wave']}"
                    kws.append({"keyword": kw, "grp": grp, "pages": client["groups"].get(grp, {}).get("pages", 1),
                                "cadence": expand.get("cadence", {}).get(str(o["wave"]), "weekly")})
    return kws


def due_today(kws, day=None):
    d = dt.date.fromisoformat(day) if day else dt.date.today()
    out = [k for k in kws if k["cadence"] == "daily" or (k["cadence"] == "weekly" and d.weekday() == 0)
           or (k["cadence"] == "monthly" and d.day == 1)]
    random.Random(d.toordinal()).shuffle(out)
    return out
