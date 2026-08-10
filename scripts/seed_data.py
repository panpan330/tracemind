"""灌入 inventory 压测数据(幂等:先清空再插入)。
用法: python scripts/seed_data.py
环境变量: BUSINESS_DB_URL / BUSINESS_DB_USER / BUSINESS_DB_PASSWORD / INVENTORY_ROWS
"""
import os
import random
import sys

import pymysql

DB_URL = os.environ.get("BUSINESS_DB_URL", "localhost:3306/tracemind_business")
HOST, rest = DB_URL.split(":", 1)
PORT, DB = rest.split("/", 1)
USER = os.environ.get("BUSINESS_DB_USER", "app_business")
PASSWORD = os.environ.get("BUSINESS_DB_PASSWORD", "app_business_pwd")
ROWS = int(os.environ.get("INVENTORY_ROWS", "500000"))
BATCH = 5000


def main() -> int:
    conn = pymysql.connect(
        host=HOST, port=int(PORT), user=USER, password=PASSWORD, database=DB, charset="utf8mb4"
    )
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM inventory")  # 先清空,保证可重复执行
            conn.commit()
            random.seed(42)
            inserted = 0
            while inserted < ROWS:
                batch = [
                    (random.randint(0, 19999), random.randint(0, 49), random.randint(0, 999))
                    for _ in range(min(BATCH, ROWS - inserted))
                ]
                cur.executemany(
                    "INSERT INTO inventory (sku_id, warehouse_id, quantity) VALUES (%s, %s, %s)",
                    batch,
                )
                conn.commit()
                inserted += len(batch)
                print(f"inserted {inserted}/{ROWS}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
