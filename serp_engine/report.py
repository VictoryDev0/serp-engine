"""Gather the data for the daily page and the weekly summary."""
import datetime as dt
import json

import store
from _common import today


def gather(conn, client, day=None):
    day = day or today()
    rows = store.day_rows(conn, client["id"], day)
    if not rows:
        last = conn.execute("SELECT MAX(day) d FROM ranks WHERE client=?", (client["id"],)).fetchone()["d"]
        if last:
            day, rows = last, store.day_rows(conn, client["id"], last)
    items = []
    for r in rows:
        items.append({"keyword": r["keyword"], "grp": r["grp"], "position": r["position"], "page": r["page"], "url": r["url"],
                      "window": r["window"], "delta7": store.delta(conn, client["id"], r["keyword"], day, 7),
                      "delta28": store.delta(conn, client["id"], r["keyword"], day, 28),
                      "ads_top": r["ads_top"], "local_pack": r["local_pack"], "in_pack": r["client_in_pack"],
                      "aio": r["ai_overview"], "in_aio": r["client_in_aio"], "aio_domains": json.loads(r["aio_domains"] or "[]"),
                      "top": json.loads(r["top10"] or "[]")[:3], "mode": r["fetch_mode"]})
    found = [i for i in items if i["position"]]
    kpi = next((i for i in items if i["grp"] == "kpi"), None)
    movers = [i for i in items if isinstance(i["delta7"], int) and i["delta7"] != 0]
    up = sorted([m for m in movers if m["delta7"] > 0], key=lambda m: -m["delta7"])[:8]
    down = sorted([m for m in movers if m["delta7"] < 0], key=lambda m: m["delta7"])[:8]
    entered = [i for i in items if i["delta7"] == "entered"]
    dropped = [i for i in items if i["delta7"] == "dropped"]
    runs = conn.execute("SELECT mode, exit_ip, fetched, blocked, started, finished FROM runs WHERE client=? AND day=? ORDER BY id DESC LIMIT 1",
                        (client["id"], day)).fetchone()
    lb = [{"host": r["host"], "n": r["c"]} for r in store.leaderboard(conn, client["id"], day)]
    week_ago = (dt.date.fromisoformat(day) - dt.timedelta(days=7)).isoformat()
    lb_prev = {r["host"]: r["c"] for r in store.leaderboard(conn, client["id"], week_ago)}
    for x in lb:
        x["delta"] = x["n"] - lb_prev.get(x["host"], 0) if lb_prev else None
    kpi_series = store.series(conn, client["id"], kpi["keyword"], 90) if kpi else []
    probes = [dict(r) for r in store.latest_probes(conn, client["id"])]
    for p in probes:
        p["cited_names"] = json.loads(p["cited_names"] or "[]")
        p["cited_domains"] = json.loads(p["cited_domains"] or "[]")
    return {"client": client, "day": day, "items": items, "found": len(found), "tracked": len(items),
            "top10": sum(1 for i in found if i["position"] <= 10), "top20": sum(1 for i in found if i["position"] <= 20),
            "avg_found": round(sum(i["position"] for i in found) / len(found), 1) if found else None,
            "kpi": kpi, "kpi_series": [(r["day"], r["position"]) for r in kpi_series],
            "up": up, "down": down, "entered": entered, "dropped": dropped,
            "aio_kw": sum(1 for i in items if i["aio"]), "aio_cited": sum(1 for i in items if i["in_aio"]),
            "pack_kw": sum(1 for i in items if i["local_pack"]), "pack_in": sum(1 for i in items if i["in_pack"]),
            "run": dict(runs) if runs else None, "leaderboard": lb, "probes": probes,
            "probe_runs": store.probe_runs(conn, client["id"]), "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}


def headline(d):
    k = d["kpi"]
    kp = f"{k['keyword']} {k['position'] or '–'}" if k else "no KPI"
    if k and isinstance(k.get("delta7"), int) and k["delta7"]:
        kp += f" ({'+' if k['delta7'] > 0 else ''}{k['delta7']} vs 7d)"
    return (f"Ranks {d['client']['label']}: {kp} · {d['top10']} Top-10 · {d['top20']} Top-20 of {d['tracked']} · "
            f"AI Overview on {d['aio_kw']} keywords, {d['aio_cited']} cited")
