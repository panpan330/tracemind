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
            cur.execute("SELECT COUNT(*) FROM inventory")
            if cur.fetchone()[0] > 0:
                print("inventory 已有数据,跳过灌入(幂等)")
                return 0
            cur.execute("DELETE FROM inventory")  # 保证可重复执行
            conn.commit()
            # (sku_id, warehouse_id) 组合唯一采样:空间 20000x50=100 万,从中随机选 ROWS 个不重复组合
            # 注意:不能加 UNIQUE 约束,否则会破坏"缺联合索引"故障场景
            random.seed(42)
            space = 20000 * 50
            combos = [(i // 50, i % 50) for i in random.sample(range(space), ROWS)]
            inserted = 0
            for i in range(0, ROWS, BATCH):
                batch = [
                    (sku, wh, random.randint(0, 999))
                    for sku, wh in combos[i:i + BATCH]
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
