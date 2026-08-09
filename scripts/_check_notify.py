# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

db = Path(r"D:\学习资料\测试文件\项目文件\tevarn-alpha-aios-0.6-sprint-20260729-0711\tevarn.db")
con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row
cur = con.cursor()
tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", tabs)
for t in tabs:
    low = t.lower()
    if any(x in low for x in ("notif", "inbox", "workforce", "message", "session")):
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({t})").fetchall()]
        n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"\n=== {t} count={n} cols={cols}")
        try:
            rows = cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 8").fetchall()
            for r in rows:
                d = dict(r)
                # truncate long fields
                for k, v in list(d.items()):
                    if isinstance(v, str) and len(v) > 180:
                        d[k] = v[:180] + "..."
                print(" ", d)
        except Exception as e:
            print(" err", e)
