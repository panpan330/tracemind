from sqlalchemy import text

from app.db.engine import get_readonly_engine
from app.tools.schemas import TABLE_REF_WHITELIST


def get_index_info(table_ref: str) -> dict:
    """E5:目标表索引元数据(information_schema.statistics)。"""
    if table_ref not in TABLE_REF_WHITELIST:
        raise ValueError(f"UNKNOWN_TABLE_REF: {table_ref}")
    sql = text("""
        SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns,
               NON_UNIQUE, INDEX_TYPE
        FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = :table
        GROUP BY INDEX_NAME, NON_UNIQUE, INDEX_TYPE
        ORDER BY INDEX_NAME
    """)
    indexes = []
    with get_readonly_engine().connect() as conn:
        for row in conn.execute(sql, {"table": table_ref}):
            indexes.append({
                "index_name": row.INDEX_NAME,
                "columns": row.columns.split(","),
                "non_unique": bool(row.NON_UNIQUE),
                "index_type": row.INDEX_TYPE,
            })
    return {"table": table_ref, "indexes": indexes}
