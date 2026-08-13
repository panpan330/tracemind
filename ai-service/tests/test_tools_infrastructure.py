# ai-service/tests/test_tools_infrastructure.py
from app.tools_infrastructure.investigation import build_investigation_ports


def test_ports_built():
    ports = build_investigation_ports()
    assert set(ports) == {"metrics", "trace", "digest", "plan", "index", "lock"}


def test_lock_port_passthrough():
    from app.tools_infrastructure.investigation import build_investigation_ports
    ports = build_investigation_ports()
    # 依赖真实 MySQL;无库时仅验证接口存在
    assert hasattr(ports["lock"], "get_lock_waiters")
    assert hasattr(ports["lock"], "get_transaction_details")
    assert hasattr(ports["metrics"], "get_metrics")
    assert hasattr(ports["index"], "get_index_info")
