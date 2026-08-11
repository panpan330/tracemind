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
    sku = int(os.environ.get("LOAD_SKU", "-1"))
    wh = int(os.environ.get("LOAD_WAREHOUSE", "-1"))
    if sku < 0:
        sku = random.randint(0, 19999)
        wh = random.randint(0, 49)
    body = f'{{"skuId":{sku},"warehouseId":{wh},"quantity":1}}'.encode()
    req = urllib.request.Request(
        f"{ORDER_URL}/api/orders/1/check-stock",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=8).read()   # 锁等待时请求阻塞,8s 超时视为故障流量
    except Exception:
        pass   # 超时/连接错误是锁故障的预期表现(计入错误率),不中断负载


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
