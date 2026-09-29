# -*- coding: utf-8 -*-
"""一次性调试:run 5437 的证据收集轮次与状态演化。用后即删。"""
import json

import pymysql

conn = pymysql.connect(host="127.0.0.1", user="tracemind_control_app",
                       password="control_app_pwd", database="tracemind_control")
with conn.cursor() as cur:
    cur.execute("SELECT round_no, phase, step_outcome, decision_json "
                "FROM incident_replay_step WHERE agent_run_id=5437 "
                "AND step_type='EVIDENCE_COLLECTION' ORDER BY id")
    rows = cur.fetchall()
    print("EVIDENCE_COLLECTION 轮数:", len(rows))
    for r in rows[:6] + rows[-3:]:
        dec = json.loads(r[3]) if r[3] and isinstance(r[3], str) else (r[3] or {})
        if not isinstance(dec, dict):
            dec = {}
        print(f"round {r[0]} {r[1]} outcome={r[2]} | "
              f"eligible={dec.get('eligibleTools')} selected={dec.get('selectedTool')}")

    cur.execute("SELECT state_after_json FROM incident_replay_step "
                "WHERE agent_run_id=5437 AND step_type='EVIDENCE_COLLECTION' "
                "AND state_after_json IS NOT NULL ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        st = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        ev = st.get("evidence") or []
        print("末轮 state evidence 条数:", len(ev))
        for e in ev[:5]:
            print("  keys:", sorted(e.keys()), "| id:", e.get("id"), "| key:", e.get("key"))
        print("gate:", st.get("evidence_gate"))
    else:
        print("无 state_after 快照")

    cur.execute("SELECT sequence_no, step_outcome FROM incident_replay_step "
                "WHERE agent_run_id=5437 ORDER BY id LIMIT 40")
    seq = [(r[0], r[1]) for r in cur.fetchall()]
    print("前 40 步 outcome 序列:", seq)
conn.close()
