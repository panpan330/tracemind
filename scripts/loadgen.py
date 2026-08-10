"""负载发生器:循环调用 order check-stock。
用法: python scripts/loadgen.py
环境变量: ORDER_SERVICE_URL / LOAD_DURATION_SECONDS / LOAD_QPS
"""
import os
import random
import sys
import time
import urllib.request

ORDER_URL = os.environ.get("ORDER_SERVICE_URL", "http://localhost:8081")
DURATION_SECONDS = int(os.environ.get("LOAD_DURATION_SECONDS", "60"))
QPS = int(os.environ.get("LOAD_QPS", "20"))


def call() -> None:
    sku = random.randint(0, 19999)
    wh = random.randint(0, 49)
    body = f'{{"skuId":{sku},"warehouseId":{wh},"quantity":1}}'.encode()
    req = urllib.request.Request(
        f"{ORDER_URL}/api/orders/1/check-stock",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req).read()


def main() -> int:
    interval = 1.0 / QPS
    deadline = time.time() + DURATION_SECONDS
    sent = 0
    while time.time() < deadline:
        call()
        sent += 1
        time.sleep(interval)
    print(f"loadgen done: {sent} requests in {DURATION_SECONDS}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
