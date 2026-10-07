#!/usr/bin/env python3
"""
网络探测看板 Web 应用（Flask，单文件）。
展示各目标最新状态 + 历史延迟趋势，按分组着色。
"""
import os
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, jsonify, g

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "data", "netprobe.db")
CFG = os.path.join(BASE, "config", "targets.json")

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _load_comments(path):
    import re
    text = open(path, encoding="utf-8").read()
    text = re.sub(r"^\s*(?:#|//).*$", "", text, flags=re.MULTILINE)
    return json.loads(text)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/latest")
def api_latest():
    """每个目标最新一条记录。"""
    db = get_db()
    rows = db.execute("""
        SELECT p.* FROM probes p
        JOIN (SELECT target_id, MAX(id) AS mid FROM probes GROUP BY target_id) m
          ON p.id = m.mid
        ORDER BY p.grp, p.name
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/history/<target_id>")
def api_history(target_id):
    """某目标最近 N 条历史。"""
    db = get_db()
    limit = 200
    rows = db.execute("""
        SELECT ts, ok, metric, http_code, error FROM probes
        WHERE target_id = ? ORDER BY id DESC LIMIT ?
    """, (target_id, limit)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/targets")
def api_targets():
    """目标清单（含 enabled 状态）。"""
    cfg = _load_comments(CFG)
    return jsonify(cfg["targets"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8088))
    app.run(host="0.0.0.0", port=port, debug=False)
