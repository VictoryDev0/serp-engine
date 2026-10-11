"""Sheet (Ranks/Weekly/AI visibility/Keywords), HTML page behind secret path, activity.json, ntfy."""
import datetime as dt
import html as H
import json
import os
import shutil

from _common import GOOGLE_HOST, HTML_DIR, ROOT, SESSION, SHOT_DIR, log

SHEET_ID = os.environ.get("SERP_SHEET_ID", "")
WEB_TOKEN = os.environ.get("SERP_WEB_TOKEN", "")
WEB_BASE = os.environ.get("SERP_WEB_BASE", "https://ranks.example.com")
WEB_ROOT = os.environ.get("SERP_WEB_ROOT", "/var/www/serp-engine")
NTFY_TOPIC = os.environ.get("SERP_NTFY_TOPIC") or os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", os.path.join(ROOT, "token.json"))


def _sheets():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        try:
            open(TOKEN_PATH, "w").write(creds.to_json())
        except Exception:  # noqa: BLE001
            pass
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _append(svc, rng, rows):
    if rows:
        svc.spreadsheets().values().append(spreadsheetId=SHEET_ID, range=rng, valueInputOption="RAW",
                                           insertDataOption="INSERT_ROWS", body={"values": rows}).execute()


def ranks_to_sheet(d):
    if not SHEET_ID:
        return None
    try:
        svc = _sheets()
        rows = [[d["day"], i["keyword"], i["grp"], i["position"] or "", 1 if i["position"] else 0, i["page"] or "",
                 i["delta7"] if i["delta7"] is not None else "", i["url"] or "", i["ads_top"], i["local_pack"], i["in_pack"],
                 i["aio"] if i["aio"] is not None else "", i["in_aio"] if i["in_aio"] is not None else "",
                 *( [t["host"] for t in i["top"]] + [""] * (3 - len(i["top"])) ), i["mode"]] for i in d["items"]]
        _append(svc, "Ranks!A1", rows)
        log(f"sheet Ranks: +{len(rows)}")
        return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
    except Exception as e:  # noqa: BLE001
        log(f"sheet ranks failed: {type(e).__name__}: {str(e)[:160]}")
        return None


def ranks_sheet_rewrite(conn, client):
    """Rewrite the Ranks tab from the DB (one row per day+keyword), so re-runs never duplicate rows."""
    if not SHEET_ID:
        return None
    import store
    try:
        rows = []
        for r in conn.execute("SELECT * FROM ranks WHERE client=? ORDER BY day, grp, keyword", (client["id"],)):
            d7 = store.delta(conn, client["id"], r["keyword"], r["day"], 7)
            top = [t["host"] for t in json.loads(r["top10"] or "[]")[:3]]
            nz = lambda v: "" if v is None else v  # noqa: E731
            rows.append([r["day"], r["keyword"], r["grp"], nz(r["position"]), r["found"], nz(r["page"]), nz(d7), r["url"] or "",
                         nz(r["ads_top"]), nz(r["local_pack"]), nz(r["client_in_pack"]), nz(r["ai_overview"]), nz(r["client_in_aio"]),
                         *(top + [""] * (3 - len(top))), r["fetch_mode"]])
        svc = _sheets()
        svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Ranks!A2:Q").execute()
        for i in range(0, len(rows), 2000):
            svc.spreadsheets().values().update(spreadsheetId=SHEET_ID, range=f"Ranks!A{2 + i}", valueInputOption="RAW",
                                               body={"values": rows[i:i + 2000]}).execute()
        log(f"sheet Ranks rewritten: {len(rows)} rows")
        return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
    except Exception as e:  # noqa: BLE001
        log(f"sheet ranks rewrite failed: {type(e).__name__}: {str(e)[:160]}")
        return None


def weekly_to_sheet(d, web_url):
    if not SHEET_ID:
        return
    try:
        svc = _sheets()
        k = d["kpi"] or {}
        best = d["up"][0] if d["up"] else None
        worst = d["down"][0] if d["down"] else None
        runs = d["run"] or {}
        _append(svc, "Weekly!A1", [[dt.date.fromisoformat(d["day"]).strftime("%G-W%V"), k.get("keyword", ""), k.get("position", ""),
                                    k.get("delta7", ""), k.get("delta28", ""), d["top10"], d["top20"], d["tracked"], d["avg_found"] or "",
                                    f"{best['keyword']} {best['delta7']:+d}" if best else "", f"{worst['keyword']} {worst['delta7']:+d}" if worst else "",
                                    d["aio_kw"], d["aio_cited"], 1 if runs and not runs.get("blocked") else 0, runs.get("blocked", 0), web_url or ""]])
        _append(svc, "AI visibility!A1", [[p["run_date"], p["surface"], p["model"], p["prompt"], p["we_cited"], p["client_how"] or "",
                                           " > ".join(x for x in p["cited_names"] if x), ", ".join(p["cited_domains"]), p["linked_page"] or "",
                                           p["client_accuracy"] or "", p["answer_type"] or ""] for p in d["probes"]
                                          if p["run_date"] == (d["probe_runs"][-1] if d["probe_runs"] else None)])
        log("sheet Weekly + AI visibility appended")
    except Exception as e:  # noqa: BLE001
        log(f"sheet weekly failed: {type(e).__name__}: {str(e)[:160]}")


def keywords_to_sheet(kws):
    if not SHEET_ID:
        return
    try:
        svc = _sheets()
        svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Keywords!A2:F").execute()
        svc.spreadsheets().values().update(spreadsheetId=SHEET_ID, range="Keywords!A2", valueInputOption="RAW",
                                           body={"values": [[k["keyword"], k["grp"], k["cadence"], k["pages"], 1, ""] for k in kws]}).execute()
    except Exception as e:  # noqa: BLE001
        log(f"sheet keywords failed: {type(e).__name__}")


# ----------------------------------------------------------------------------- HTML
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mut:#6b7280;--line:#e5e7eb;--acc:#2563eb;--good:#15803d;--mid:#b45309;--low:#9ca3af}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--card:#171a21;--ink:#e6e7eb;--mut:#9aa0ab;--line:#262a33;--acc:#7aa2ff;--good:#4ade80;--mid:#fbbf24;--low:#6b7280}}
:root[data-theme=dark]{--bg:#0f1115;--card:#171a21;--ink:#e6e7eb;--mut:#9aa0ab;--line:#262a33;--acc:#7aa2ff;--good:#4ade80;--mid:#fbbf24;--low:#6b7280}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 18px 60px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;margin:30px 0 10px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}.meta{color:var(--mut);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}.tile .v{font-size:26px;font-weight:700}.tile .l{font-size:12px;color:var(--mut)}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:grid;grid-template-columns:1fr 2fr;gap:16px;align-items:center}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:14px}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--mut);text-transform:uppercase}
.pos{display:inline-block;min-width:34px;text-align:center;border-radius:8px;padding:2px 6px;font-weight:700}
.g{background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good)}.m{background:color-mix(in srgb,var(--mid) 18%,transparent);color:var(--mid)}.l{background:color-mix(in srgb,var(--low) 18%,transparent);color:var(--low)}
.up{color:var(--good)}.dn{color:#dc2626}.flag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--mut);margin-right:4px}
.flag.on{border-color:var(--acc);color:var(--acc)}.tbl{overflow-x:auto}.small{font-size:12px;color:var(--mut)}
"""


def _pos(p):
    if not p:
        return '<span class="pos l">–</span>'
    c = "g" if p <= 10 else ("m" if p <= 20 else "l")
    return f'<span class="pos {c}">{p}</span>'


def _delta(x):
    if x is None:
        return ""
    if x == "entered":
        return '<span class=up>new</span>'
    if x == "dropped":
        return '<span class=dn>out</span>'
    return f'<span class={"up" if x > 0 else "dn"}>{x:+d}</span>' if x else "±0"


def _spark(series, w=420, h=70):
    pts = [(i, p) for i, (_, p) in enumerate(series) if p]
    if len(pts) < 2:
        return f'<svg width="{w}" height="{h}"><text x="6" y="40" fill="#9ca3af" font-size="12">Sparkline from day 2</text></svg>'
    n = len(series)
    mx = max(p for _, p in pts)
    mn = min(p for _, p in pts)
    rng = max(mx - mn, 5)
    def xy(i, p):
        return (6 + i * (w - 12) / max(n - 1, 1), 6 + (p - mn) * (h - 12) / rng)
    d = " ".join(f"{'M' if k == 0 else 'L'}{xy(i, p)[0]:.1f},{xy(i, p)[1]:.1f}" for k, (i, p) in enumerate(pts))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><path d="{d}" fill="none" stroke="#2563eb" stroke-width="2"/>'
            f'<text x="{w-40}" y="12" font-size="11" fill="#9ca3af">Pos {mn}</text><text x="{w-40}" y="{h-2}" font-size="11" fill="#9ca3af">Pos {mx}</text></svg>')


def _rows(items):
    out = []
    for i in items:
        flags = "".join(f'<span class="flag{" on" if v else ""}">{n}</span>' for n, v in
                        (("Ads", i["ads_top"]), ("Local", i["local_pack"]), ("in pack", i["in_pack"]), ("AI Overview", i["aio"]), ("cited", i["in_aio"])))
        top = ", ".join(t["host"] for t in i["top"])
        out.append(f"<tr><td>{H.escape(i['keyword'])}</td><td>{_pos(i['position'])}</td><td>{_delta(i['delta7'])}</td>"
                   f"<td>{_delta(i['delta28'])}</td><td class=small>{H.escape(top)}</td><td>{flags}</td></tr>")
    return "".join(out)


def render_html(d, narrative=""):
    c = d["client"]
    k = d["kpi"]
    parts = [f"<title>Ranks {H.escape(c['label'])}</title><style>{CSS}</style><div class=wrap>",
             f"<h1>Rank Tracker · {H.escape(c['label'])}</h1><div class=meta>{H.escape(d['day'])} · {d['tracked']} keywords · {H.escape(GOOGLE_HOST)}"
             + (f", {H.escape(c.get('location', {}).get('city', ''))}" if c.get("location", {}).get("city") else "") + " · "
             f"{H.escape((d['run'] or {}).get('mode') or '')} via {H.escape((d['run'] or {}).get('exit_ip') or '')}"
             + (" · <b style='color:#dc2626'>run blocked</b>" if (d['run'] or {}).get('blocked') else "") + "</div>"]
    if narrative:
        parts.append(f'<div class=tile style="margin-top:14px">{H.escape(narrative)}</div>')
    if k:
        shot = f"shots/{d['day']}_{''.join(ch if ch.isalnum() else '-' for ch in k['keyword'])[:60]}.png"
        parts.append(f'<h2>KPI</h2><div class=kpi><div><div class=l>{H.escape(k["keyword"])}</div><div style="font-size:40px;font-weight:700">{k["position"] or "–"}</div>'
                     f'<div>7d {_delta(k["delta7"])} · 28d {_delta(k["delta28"])}'
                     + (f' · Goal: {H.escape(c["kpi_goal"])}' if c.get("kpi_goal") else "") + '</div>'
                     f'<div class=small><a href="{shot}" target=_blank>Screenshot today</a></div></div><div>{_spark(d["kpi_series"])}</div></div>')
    parts.append('<div class=tiles>' + "".join(f'<div class=tile><div class=v>{v}</div><div class=l>{l}</div></div>' for v, l in
                 ((d["top10"], "in Top 10"), (d["top20"], "in Top 20"), (f"{d['found']}/{d['tracked']}", "found (in window)"),
                  (d["avg_found"] or "–", "avg position (found)"), (f"{d['pack_in']}/{d['pack_kw']}", "in local pack / pack shown"),
                  (f"{d['aio_cited']}/{d['aio_kw']}", "cited in AI Overview / shown"))) + '</div>')
    if d["up"] or d["down"] or d["entered"] or d["dropped"]:
        parts.append("<h2>Movers (7 days)</h2><div class=tbl><table><tr><th>Keyword</th><th>Pos</th><th>7d</th><th>28d</th><th>Top 3</th><th></th></tr>"
                     + _rows(d["up"] + d["entered"] + d["down"] + d["dropped"]) + "</table></div>")
    groups = list(c.get("groups", {}).keys()) + sorted({i["grp"] for i in d["items"]} - set(c.get("groups", {})))
    for grp in groups:
        label = c.get("groups", {}).get(grp, {}).get("label", grp)
        items = [i for i in d["items"] if i["grp"] == grp]
        if items:
            parts.append(f"<h2>{H.escape(label)} ({len(items)})</h2><div class=tbl><table><tr><th>Keyword</th><th>Pos</th><th>7d</th><th>28d</th><th>Top 3</th><th>SERP features</th></tr>{_rows(items)}</table></div>")
    if d["leaderboard"]:
        parts.append("<h2>Competitors in the Top 10</h2><div class=tbl><table><tr><th>Domain</th><th>Top-10 hits today</th><th>vs 7d</th></tr>"
                     + "".join(f"<tr><td>{H.escape(x['host'])}</td><td>{x['n']}</td><td>{_delta(x['delta']) if x['delta'] is not None else ''}</td></tr>" for x in d["leaderboard"]) + "</table></div>")
    if d["probes"]:
        parts.append(f"<h2>AI visibility (run {H.escape(d['probes'][0]['run_date'])})</h2><div class=tbl><table><tr><th>Prompt</th><th>Surface</th><th>Client cited</th><th>Named providers (in order)</th><th>Domains</th></tr>"
                     + "".join(f"<tr><td>{H.escape(p['prompt'])}</td><td>{H.escape(p['surface'])}</td><td>{'<b class=up>yes</b> · ' + H.escape(p['client_how'] or '') if p['we_cited'] else 'no'}</td>"
                               f"<td class=small>{H.escape(' › '.join(x for x in p['cited_names'] if x))}</td><td class=small>{H.escape(', '.join(p['cited_domains'][:8]))}</td></tr>" for p in d["probes"]) + "</table></div>")
    parts.append(f"<div class=meta style='margin-top:24px'>Generated {H.escape(d['generated_at'])} · Archive: <a href='./'>today</a></div></div>")
    os.makedirs(HTML_DIR, exist_ok=True)
    path = os.path.join(HTML_DIR, f"{c['id']}_{d['day']}.html")
    open(path, "w", encoding="utf-8").write("\n".join(parts))
    return path


def publish(d, path):
    if not WEB_TOKEN or not os.path.isdir(WEB_ROOT):
        log("web: no token/docroot, skipped")
        return None
    c = d["client"]
    base = os.path.join(WEB_ROOT, WEB_TOKEN, c["id"])
    os.makedirs(os.path.join(base, "shots"), exist_ok=True)
    shutil.copy(path, os.path.join(base, "index.html"))
    shutil.copy(path, os.path.join(base, f"{d['day']}.html"))
    if os.path.isdir(SHOT_DIR):
        for f in os.listdir(SHOT_DIR):
            if f.startswith(d["day"]):
                shutil.copy(os.path.join(SHOT_DIR, f), os.path.join(base, "shots", f))
    k = d["kpi"] or {}
    wins = sum(1 for i in d["items"] if i["delta7"] == "entered" or (isinstance(i["delta7"], int) and i["delta7"] >= 5))
    act = {"ok": True, "total_events": d["tracked"], "meaningful_events": wins, "last_activity_ts": d["generated_at"],
           "last_modified": {"ts": d["generated_at"], "source": "serp-engine"},
           "kpi": {"keyword": k.get("keyword"), "position": k.get("position"), "delta_7d": k.get("delta7")}}
    json.dump(act, open(os.path.join(base, "activity.json"), "w"))
    json.dump({kk: v for kk, v in d.items() if kk not in ("client",)}, open(os.path.join(base, "data.json"), "w"), ensure_ascii=False, default=str)
    open(os.path.join(WEB_ROOT, "index.html"), "w").write("<title>Not found</title>")
    url = f"{WEB_BASE}/{WEB_TOKEN}/{c['id']}/"
    log(f"web: {url}")
    return url


def ntfy(title, msg, click=None, priority="default"):
    if not NTFY_TOPIC:
        return
    try:
        h = {"Title": title, "Tags": "chart_with_upwards_trend", "Priority": priority}
        if click:
            h["Click"] = click
        SESSION.post(f"{NTFY_SERVER}/{NTFY_TOPIC}", data=msg.encode("utf-8"), headers=h, timeout=20)
    except Exception as e:  # noqa: BLE001
        log(f"ntfy failed: {type(e).__name__}")
