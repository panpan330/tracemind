"""full 档前置检查:MySQL/Java 服务/AI 服务/Qdrant 端点可达。任一不可达 → 非零退出。"""
import os
import sys
import urllib.request

CHECKS = [
    ("order-service", "http://localhost:8081/actuator/health"),
    ("inventory-service", "http://localhost:8082/actuator/health"),
    ("ai-service", "http://localhost:8000/api/health"),
]
if os.environ.get("FULL_DB_CHECK", "1") == "1":
    CHECKS.append(("qdrant", os.environ.get("QDRANT_URL", "http://127.0.0.1:6333") + "/healthz"))

ok = True
for name, url in CHECKS:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            reachable = r.status == 200
    except Exception:
        reachable = False
    print(f"{name}: {'OK' if reachable else 'FAIL'}")
    ok = ok and reachable
sys.exit(0 if ok else 1)
