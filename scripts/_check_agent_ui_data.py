# -*- coding: utf-8 -*-
import json
import sqlite3
from pathlib import Path

db = Path(r"D:\学习资料\测试文件\项目文件\tevarn-alpha-aios-0.6-sprint-20260729-0711\tevarn.db")
con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row

print("=== identities ===")
for r in con.execute(
    "SELECT id, name, default_token_budget, credit_score, status FROM agent_identities"
):
    print(dict(r))

print("\n=== inbox by identity ===")
for r in con.execute(
    """
    SELECT identity_id, status, COUNT(*) c
    FROM agent_inbox_items
    GROUP BY identity_id, status
    """
):
    print(dict(r))

print("\n=== kernel_processes tokens ===")
try:
    for r in con.execute(
        """
        SELECT process_id, identity_key, tokens_used, token_budget, state
        FROM kernel_processes
        ORDER BY rowid DESC LIMIT 12
        """
    ):
        print(dict(r))
except Exception as e:
    print("err", e)

print("\n=== identity_memory kinds ===")
try:
    for r in con.execute(
        "SELECT identity_id, kind, COUNT(*) c FROM identity_memory GROUP BY identity_id, kind"
    ):
        print(dict(r))
except Exception as e:
    print("err", e)

print("\n=== evolution proposals ===")
try:
    for r in con.execute(
        "SELECT id, identity_id, kind, status, title FROM agent_evolution_proposals LIMIT 10"
    ):
        print(dict(r))
except Exception as e:
    print("err", e)
