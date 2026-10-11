"""SERP engine CLI (always exits 0).

  run.py --daily [--client example] [--limit N] [--mode chrome|http|direct] [--no-sheet --no-publish --no-push]
  run.py --weekly            # daily if missing, then AI probes + weekly summary + Sheet + ntfy
  run.py --ai-probe [--surface perplexity|openai_search|google_ai_mode]
  run.py --probe             # smoke: proxy, chrome, first client keyword parsed
  run.py --dump "<keyword>"  # one keyword, print parsed result
  run.py --republish | --status | --unpause | --import-baseline <path> --date YYYY-MM-DD
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_probe  # noqa: E402
import blockguard  # noqa: E402
import deliver  # noqa: E402
import keywords as kwmod  # noqa: E402
import report  # noqa: E402
import serp  # noqa: E402
import store  # noqa: E402
from _common import PROXY, log, now_iso, proxy_ip, today  # noqa: E402


def pick_mode(forced=None):
    ip = proxy_ip()
    if forced == "direct" or (forced is None and not ip):
        return "direct", None
    if forced == "http":
        return "http", ip
    return "chrome", ip


def _chrome(mode):
    if mode != "chrome":
        return None
    from chrome_cdp import Chrome
    return Chrome(proxy=PROXY.replace("socks5h://", "socks5://")).start()


def do_daily(conn, client, mode=None, limit=0, shot_kpi=True):
    day = today()
    if (until := blockguard.paused(conn)):
        log(f"paused until {until}; skipping daily")
        return None
    kws = kwmod.all_keywords(client)
    store.upsert_keywords(conn, client["id"], kws)
    due = kwmod.due_today(kws)
    if limit:
        due = due[:limit]
    mode, ip = pick_mode(mode)
    if mode == "direct":
        due = [k for k in due if k["grp"] in ("kpi", "head")]
        log("proxy down, direct mode with reduced keyword set")
        deliver.ntfy("SERP engine", f"{client['label']}: proxy down, direct mode with {len(due)} keywords", priority="low")
    started = now_iso()
    chrome = None
    fetched = blocked = soft = 0
    try:
        chrome = _chrome(mode)
        if chrome is None and mode == "chrome":
            mode = "http"
        for k in due:
            if store.has_rows(conn, client["id"], day) and conn.execute(
                    "SELECT 1 FROM ranks WHERE client=? AND day=? AND keyword=?", (client["id"], day, k["keyword"])).fetchone():
                continue
            try:
                res, blk = serp.fetch_keyword(client, k, mode, chrome=chrome, use_proxy=(mode != "direct"),
                                              shot=(shot_kpi and k["grp"] == "kpi"))
            except Exception as e:  # noqa: BLE001
                log(f"fetch crashed '{k['keyword']}': {type(e).__name__}: {str(e)[:120]}")
                if mode == "chrome":
                    try:
                        chrome.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    chrome, mode = None, "http"
                    log("continuing in http mode")
                continue
            if blk:
                blocked += 1
                if blocked >= 2:
                    blockguard.pause(conn, 12, f"{mode} blocked twice ({ip})")
                    deliver.ntfy("SERP engine paused", f"{client['label']}: captcha via {mode} ({ip}) after {fetched}/{len(due)} keywords", priority="high")
                    break
                import time
                time.sleep(120)
                continue
            if res.get("soft_block") or not res.get("organic"):
                soft += 1
                log(f"{k['keyword']!r}: no organic results parsed ({mode}), not saved")
                serp.pace()
                continue
            store.save_rank(conn, client["id"], day, k["keyword"], k["grp"], res, mode, ip, res.get("html_path"))
            fetched += 1
            log(f"{k['keyword']!r}: pos {res['position']} p{res['page']} · ads {res['ads_top']} pack {res['local_pack']}/{res['client_in_pack']} aio {res['ai_overview']}/{res['client_in_aio']}")
            serp.pace()
    finally:
        if chrome:
            chrome.stop()
    store.record_run(conn, client["id"], "daily", day, started, mode, ip, fetched, 1 if blocked >= 2 else 0,
                     {"due": len(due), "fetched": fetched, "blocked": blocked, "soft": soft})
    if soft >= 3:
        deliver.ntfy("SERP engine", f"{client['label']}: {soft} SERPs with 0 organic results, parser drift?", priority="low")
    log(f"daily done: {fetched}/{len(due)} fetched, mode {mode}, blocked {blocked}, soft {soft}")
    return fetched


def do_publish(conn, client, sheet=True, publish=True):
    d = report.gather(conn, client)
    path = deliver.render_html(d)
    url = deliver.publish(d, path) if publish else None
    if sheet:
        deliver.ranks_sheet_rewrite(conn, client)
    return d, url


def do_ai_probe(conn, client, surfaces, mode=None):
    run_date = today()
    prompts = client.get("ai_prompts", [])
    n = 0
    for s in surfaces:
        if s in ("perplexity", "openai_search"):
            n += ai_probe.probe_openrouter(conn, client, run_date, s, prompts) or 0
        if s == "gemini_grounded":
            n += ai_probe.probe_gemini_grounded(conn, client, run_date, prompts) or 0
    if "google_ai_mode" in surfaces and (until := blockguard.paused(conn)):
        log(f"google_ai_mode skipped: engine paused until {until}")
    elif "google_ai_mode" in surfaces:
        m, ip = pick_mode(mode)
        if m == "chrome":
            ch = None
            try:
                ch = _chrome("chrome")
                n += ai_probe.probe_google_ai_mode(conn, client, run_date, prompts, ch) or 0
            except Exception as e:  # noqa: BLE001
                log(f"google_ai_mode failed: {type(e).__name__}: {str(e)[:120]}")
            finally:
                if ch:
                    ch.stop()
        else:
            log("google_ai_mode skipped (no proxy/chrome)")
    # A probe run that stores nothing used to finish "successfully" and append an
    # empty row to the Sheet, which is how the AI-visibility KPI once stayed blank for
    # two weeks without a single alert. Silence is now the alarm.
    if not n:
        log(f"AI PROBE EMPTY: 0 answers stored across {len(surfaces)} surfaces")
        try:
            deliver.ntfy(f"SERP {client['label']}", "AI visibility: 0 answers stored, KPI stays empty")
        except Exception:  # noqa: BLE001
            pass
    return n


def main():
    ap = argparse.ArgumentParser()
    for f in ("daily", "weekly", "ai-probe", "probe", "republish", "status", "unpause", "no-sheet", "no-publish", "no-push"):
        ap.add_argument("--" + f, action="store_true")
    ap.add_argument("--client", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mode", default=None)
    ap.add_argument("--surface", default=None)
    ap.add_argument("--dump", default=None)
    ap.add_argument("--import-baseline", default=None)
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    conn = store.connect()
    try:
        for client in kwmod.load_clients(a.client):
            if a.status:
                d = report.gather(conn, client)
                print(report.headline(d)); print("paused:", blockguard.paused(conn)); print("last run:", d["run"])
                for i in d["items"]:
                    print(f"  {i['position'] or '–':>3}  {i['keyword']}  [{i['grp']}] 7d={i['delta7']}")
            if a.unpause:
                blockguard.unpause(conn); print("unpaused")
            if a.import_baseline:
                ai_probe.import_baseline(conn, client, a.import_baseline, a.date or today())
            if a.probe:
                mode, ip = pick_mode(a.mode)
                print("mode", mode, "exit_ip", ip)
                ch = _chrome(mode)
                try:
                    first = (kwmod.all_keywords(client) or [{"keyword": "weather", "grp": "head"}])[0]
                    res, blk = serp.fetch_keyword(client, {"keyword": first["keyword"], "pages": 1, "grp": first["grp"]}, mode, chrome=ch, use_proxy=(mode != "direct"))
                    print("blocked" if blk else json.dumps({k: v for k, v in res.items() if k not in ("organic", "aio_text", "html_path")}, ensure_ascii=False, indent=1)[:2500])
                    if not blk:
                        print("organic:", [(o["pos"], o["host"]) for o in res["organic"]])
                finally:
                    if ch:
                        ch.stop()
            if a.dump:
                mode, ip = pick_mode(a.mode)
                ch = _chrome(mode)
                try:
                    kw = next((k for k in kwmod.all_keywords(client) if k["keyword"] == a.dump), {"keyword": a.dump, "pages": 2, "grp": "adhoc"})
                    res, blk = serp.fetch_keyword(client, kw, mode, chrome=ch, use_proxy=(mode != "direct"), shot=True)
                    print("blocked" if blk else json.dumps({k: v for k, v in res.items() if k != "aio_text"}, ensure_ascii=False, indent=1)[:4000])
                finally:
                    if ch:
                        ch.stop()
            fetched = None
            if a.daily or a.weekly:
                if not (a.weekly and store.has_rows(conn, client["id"], today()) and not a.daily):
                    fetched = do_daily(conn, client, mode=a.mode, limit=a.limit)
            if a.ai_probe or a.weekly:
                surfaces = [a.surface] if a.surface else ["google_ai_mode", "perplexity"]  # gemini_grounded needs billing (429 on free tier)
                do_ai_probe(conn, client, surfaces, mode=a.mode)
            if a.daily or a.weekly or a.republish or a.ai_probe:
                # Sheet rows only when this run actually fetched something (a paused run would re-append yesterday's rows).
                d, url = do_publish(conn, client, sheet=not a.no_sheet, publish=not a.no_publish)  # rewrite is idempotent
                if a.weekly:
                    if not a.no_sheet:
                        deliver.weekly_to_sheet(d, url)
                    deliver.keywords_to_sheet(kwmod.all_keywords(client))
                    if not a.no_push:
                        deliver.ntfy(f"Ranks {client['label']}", report.headline(d), click=url)
                elif a.daily and d["kpi"] and d["kpi"]["position"] and d["kpi"]["position"] <= 10 and not a.no_push:
                    deliver.ntfy(f"Ranks {client['label']}", f"KPI in the Top 10: {d['kpi']['keyword']} = {d['kpi']['position']}", click=url)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL {type(e).__name__}: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
