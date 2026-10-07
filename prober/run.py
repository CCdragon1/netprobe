#!/usr/bin/env python3
"""
定时探测执行器：读取目标清单 -> 探测 -> 结果落 SQLite。
配合 systemd-timer 或 cron 周期调用。
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from probe import run_all, load_json_with_comments

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(BASE, "config", "targets.json")
DB = os.path.join(BASE, "data", "netprobe.db")


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS probes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            target_id TEXT NOT NULL,
            name TEXT,
            grp TEXT,
            typ TEXT,
            host TEXT,
            port INTEGER,
            ok INTEGER,
            metric REAL,
            metric_label TEXT,
            http_code INTEGER,
            loss REAL,
            error TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_probes_target_ts ON probes(target_id, ts)")
    conn.commit()


def store(conn, results):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for r in results:
        rows.append((
            r["time"], r["id"], r["name"], r["group"], r["type"], r["host"],
            r["port"], 1 if r["ok"] else 0, r["metric"], r["metric_label"],
            r["extra"].get("http_code"), r["extra"].get("loss"), r["error"]
        ))
    conn.executemany("""
        INSERT INTO probes (ts, target_id, name, grp, typ, host, port, ok, metric,
                            metric_label, http_code, loss, error)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def main():
    with open(CFG, encoding="utf-8") as f:
        cfg = load_json_with_comments(CFG)
    conn = sqlite3.connect(DB)
    init_db(conn)
    t0 = time.time()
    results = run_all(cfg["targets"])
    store(conn, results)
    conn.close()
    elapsed = time.time() - t0
    ok_cnt = sum(1 for r in results if r["ok"])
    print(f"\n本轮完成：{len(results)} 目标，{ok_cnt} 成功，耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
