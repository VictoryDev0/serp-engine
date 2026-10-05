"""SQLite store for the SERP engine: keywords, daily ranks, per-position results, AI probes, runs, state."""
import datetime as dt
import json
import os
import sqlite3

from _common import DB_PATH, TMP, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (client TEXT, keyword TEXT, grp TEXT, pages INTEGER, cadence TEXT,
  active INTEGER DEFAULT 1, added TEXT, PRIMARY KEY(client, keyword));
CREATE TABLE IF NOT EXISTS ranks (client TEXT, day TEXT, keyword TEXT, grp TEXT,
  position INTEGER, url TEXT, page INTEGER, found INTEGER, window INTEGER,
  ads_top INTEGER, ads_count INTEGER, local_pack INTEGER, client_in_pack INTEGER,
  ai_overview INTEGER, client_in_aio INTEGER, aio_domains TEXT, top10 TEXT,
  fetch_mode TEXT, exit_ip TEXT, html_path TEXT, fetched_at TEXT,
  PRIMARY KEY(client, day, keyword));
CREATE TABLE IF NOT EXISTS serp_results (client TEXT, day TEXT, keyword TEXT, pos INTEGER, kind TEXT,
  host TEXT, url TEXT, title TEXT, PRIMARY KEY(client, day, keyword, kind, pos));
CREATE TABLE IF NOT EXISTS ai_probes (id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT, run_date TEXT,
  surface TEXT, prompt TEXT, model TEXT, cited_domains TEXT, cited_names TEXT, we_cited INTEGER,
  linked_page TEXT, client_how TEXT, client_accuracy TEXT, answer_type TEXT, text TEXT, raw_json TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT, kind TEXT, day TEXT,
  started TEXT, finished TEXT, mode TEXT, exit_ip TEXT, fetched INTEGER, blocked INTEGER, stats TEXT);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_ranks_kw ON ranks(client, keyword, day);
CREATE INDEX IF NOT EXISTS ix_sr_host ON serp_results(client, host, day);
"""


def connect():
    os.makedirs(TMP, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_state(conn, key, default=None):
    r = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_state(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, json.dumps(value)))
    conn.commit()


def upsert_keywords(conn, client, kws):
    """kws: list of dicts keyword/grp/pages/cadence. Missing ones become inactive, history stays."""
    active = set()
    for k in kws:
        active.add(k["keyword"])
        conn.execute("INSERT INTO keywords(client,keyword,grp,pages,cadence,active,added) VALUES(?,?,?,?,?,1,?)"
                     " ON CONFLICT(client,keyword) DO UPDATE SET grp=excluded.grp, pages=excluded.pages,"
                     " cadence=excluded.cadence, active=1", (client, k["keyword"], k["grp"], k["pages"], k["cadence"], now_iso()))
    rows = conn.execute("SELECT keyword FROM keywords WHERE client=? AND active=1", (client,)).fetchall()
    for r in rows:
        if r["keyword"] not in active:
            conn.execute("UPDATE keywords SET active=0 WHERE client=? AND keyword=?", (client, r["keyword"]))
    conn.commit()


def save_rank(conn, client, day, kw, grp, res, mode, ip, html_path):
    conn.execute("INSERT OR REPLACE INTO ranks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (client, day, kw, grp, res.get("position"), res.get("url"), res.get("page"), 1 if res.get("position") else 0,
                  res.get("window"), res.get("ads_top"), res.get("ads_count"), res.get("local_pack"), res.get("client_in_pack"),
                  res.get("ai_overview"), res.get("client_in_aio"), json.dumps(res.get("aio_domains") or []),
                  json.dumps(res.get("top10") or [], ensure_ascii=False), mode, ip, html_path, now_iso()))
    conn.execute("DELETE FROM serp_results WHERE client=? AND day=? AND keyword=?", (client, day, kw))
    for kind, items in (("organic", res.get("organic") or []), ("ad", res.get("ads") or []), ("aio", res.get("aio_links") or [])):
        for i, it in enumerate(items, 1):
            conn.execute("INSERT OR REPLACE INTO serp_results VALUES(?,?,?,?,?,?,?,?)",
                         (client, day, kw, it.get("pos", i), kind, it.get("host"), it.get("url"), it.get("title")))
    conn.commit()


def has_rows(conn, client, day):
    return conn.execute("SELECT COUNT(*) c FROM ranks WHERE client=? AND day=?", (client, day)).fetchone()["c"]


def position_on_or_before(conn, client, kw, day):
    r = conn.execute("SELECT position, day FROM ranks WHERE client=? AND keyword=? AND day<=? ORDER BY day DESC LIMIT 1",
                     (client, kw, day)).fetchone()
    return (r["position"], r["day"]) if r else (None, None)


def delta(conn, client, kw, day, days_back):
    ref_day = (dt.date.fromisoformat(day) - dt.timedelta(days=days_back)).isoformat()
    cur, _ = position_on_or_before(conn, client, kw, day)
    ref, ref_d = position_on_or_before(conn, client, kw, ref_day)
    if ref_d is None or ref_d == day:
        return None
    if cur is None and ref is None:
        return None
    if cur is None:
        return "dropped"
    if ref is None:
        return "entered"
    return ref - cur  # positive = improved


def day_rows(conn, client, day):
    return conn.execute("SELECT * FROM ranks WHERE client=? AND day=? ORDER BY grp, keyword", (client, day)).fetchall()


def series(conn, client, kw, days=90):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    return conn.execute("SELECT day, position FROM ranks WHERE client=? AND keyword=? AND day>=? ORDER BY day",
                        (client, kw, since)).fetchall()


def leaderboard(conn, client, day, top=10):
    return conn.execute("SELECT host, COUNT(*) c FROM serp_results WHERE client=? AND day=? AND kind='organic' AND pos<=?"
                        " GROUP BY host ORDER BY c DESC LIMIT 15", (client, day, top)).fetchall()


def record_run(conn, client, kind, day, started, mode, ip, fetched, blocked, stats):
    conn.execute("INSERT INTO runs(client,kind,day,started,finished,mode,exit_ip,fetched,blocked,stats) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (client, kind, day, started, now_iso(), mode, ip, fetched, blocked, json.dumps(stats)))
    conn.commit()


def save_probe(conn, client, run_date, surface, prompt, model, res, text, raw):
    conn.execute("INSERT INTO ai_probes(client,run_date,surface,prompt,model,cited_domains,cited_names,we_cited,linked_page,"
                 "client_how,client_accuracy,answer_type,text,raw_json,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (client, run_date, surface, prompt, model, json.dumps(res.get("cited_domains") or []),
                  json.dumps(res.get("cited_names") or [], ensure_ascii=False), 1 if res.get("we_cited") else 0,
                  res.get("linked_page"), res.get("client_how"), res.get("client_accuracy"), res.get("answer_type"),
                  text, json.dumps(raw, ensure_ascii=False)[:20000], now_iso()))
    conn.commit()


def latest_probes(conn, client):
    return conn.execute("SELECT * FROM ai_probes WHERE client=? AND run_date=(SELECT MAX(run_date) FROM ai_probes WHERE client=?)"
                        " ORDER BY prompt, surface", (client, client)).fetchall()


def probe_runs(conn, client):
    return [r["run_date"] for r in conn.execute("SELECT DISTINCT run_date FROM ai_probes WHERE client=? ORDER BY run_date", (client,))]
