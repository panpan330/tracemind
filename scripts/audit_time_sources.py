"""V2.0-A 时间语义审计(只读):识别存量时间来源与偏差,不做任何数据修改。

用法:
  TRACEMIND_MIGRATE_DB_URL=mysql+pymysql://user:pwd@host:3306/tracemind_control \
      python scripts/audit_time_sources.py

输出:
  1) 服务器/会话时区与 NOW() vs UTC_TIMESTAMP 偏差;
  2) DATETIME/TIMESTAMP 列中带 CURRENT_TIMESTAMP 默认(DB 侧填时间)的清单;
  3) 关键业务表 max(created_at/started_at) 与 UTC 当前时间差(>1h 判 skewed)。
退出码:发现 skewed 时间来源时为 2,正常 0(仅报告,不修复)。
"""
import os
import sys
from datetime import datetime, timezone

import pymysql

KEY_TABLES = {
    ("tracemind_control", "agent_run"): ("started_at", "finished_at"),
    ("tracemind_control", "approval"): ("created_at", "expires_at"),
    ("tracemind_control", "tool_call"): ("created_at",),
    ("tracemind_control", "tool_call_attempt"): ("started_at", "completed_at"),
    ("tracemind_control", "incident_event"): ("occurred_at",),
    ("tracemind_control", "observation_query"): ("queried_at",),
    ("tracemind_control", "eval_run"): ("created_at",),
}


def main() -> int:
    url = os.environ.get("TRACEMIND_MIGRATE_DB_URL")
    if not url:
        print("TRACEMIND_MIGRATE_DB_URL 未设置", file=sys.stderr)
        return 1
    rest = url.split("://", 1)[1]
    cred, host = rest.rsplit("@", 1)
    user, _, pwd = cred.partition(":")
    host, _, db = host.partition("/")
    db = db or "tracemind_control"
    port = 3306
    if ":" in host:
        host, _, port_s = host.partition(":")
        port = int(port_s)
    conn = pymysql.connect(host=host, port=port, user=user, password=pwd,
                           database=db, charset="utf8mb4")
    skewed = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT @@session.time_zone, @@global.time_zone")
            sess, glob = cur.fetchone()
            print(f"[tz] session={sess} global={glob}")
            cur.execute("SELECT NOW(), UTC_TIMESTAMP()")
            now, utc = cur.fetchone()
            drift = (now - utc).total_seconds()
            print(f"[drift] NOW()={now} UTC_TIMESTAMP()={utc} delta={drift:.0f}s")
            if abs(drift) > 60:
                skewed.append(f"服务器 NOW() 与 UTC 偏差 {drift:.0f}s(未设 default-time-zone=+00:00?)")

            cur.execute("""
                SELECT table_schema, table_name, column_name, column_type, column_default
                FROM information_schema.columns
                WHERE data_type IN ('datetime','timestamp')
                  AND column_default LIKE 'CURRENT_TIMESTAMP%%'
                ORDER BY table_schema, table_name""")
            rows = cur.fetchall()
            print(f"[db-default] {len(rows)} 个由 DB 侧填时间的列(CURRENT_TIMESTAMP 默认):")
            for schema, table, col, ctype, dflt in rows:
                print(f"  - {schema}.{table}.{col} {ctype} DEFAULT {dflt}")

            print("[freshness] 关键表最新时间 vs UTC 当前:")
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            for (schema, table), cols in KEY_TABLES.items():
                for col in cols:
                    try:
                        cur.execute(f"SELECT MAX({col}) FROM {schema}.{table}")
                        mx = cur.fetchone()[0]
                    except pymysql.err.MySQLError as e:
                        print(f"  - {schema}.{table}.{col}: 跳过({e.args[0]})")
                        continue
                    if mx is None:
                        print(f"  - {schema}.{table}.{col}: (空)")
                        continue
                    if mx.tzinfo is not None:
                        mx = mx.replace(tzinfo=None)
                    age_h = (now_utc - mx).total_seconds() / 3600
                    flag = ""
                    if age_h < -1:
                        flag = "  <-- SKEWED(时间在 UTC 未来)"
                        skewed.append(f"{schema}.{table}.{col} 最新值在 UTC 未来 {age_h:.1f}h")
                    elif age_h > 24 * 30:
                        flag = "  (陈旧但无害:历史数据)"
                    print(f"  - {schema}.{table}.{col}: {mx} ({age_h:.1f}h 前){flag}")
    finally:
        conn.close()
    print()
    if skewed:
        print("发现疑似时区偏差来源:")
        for s in skewed:
            print(f"  * {s}")
        return 2
    print("未发现时间来源偏差(应用侧 naive UTC 与 DB 会话 UTC 一致)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
