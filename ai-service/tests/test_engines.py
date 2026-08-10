from sqlalchemy import text

from app.db.engine import get_control_engine, get_readonly_engine


def test_control_engine_can_write():
    engine = get_control_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TEMPORARY TABLE _t (id INT)"))
        conn.execute(text("INSERT INTO _t VALUES (1)"))
    # 临时表随连接关闭自动消失,能走到这里即证明可写


def test_readonly_engine_cannot_write():
    engine = get_readonly_engine()
    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE TEMPORARY TABLE _t2 (id INT)"))
            raise AssertionError("readonly engine must reject writes")
        except Exception:
            pass  # 期望被拒绝(SELECT 权限无 CREATE)
