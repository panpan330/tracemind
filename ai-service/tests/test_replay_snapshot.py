from app.replay.snapshot import ReplaySnapshotFactory, canonical_json, snapshot_hash


def _state(**over):
    base = {
        "incident_id": 1, "run_id": 2, "status": "investigating",
        "hypotheses": [{"id": "h1", "description": "缺索引", "status": "proposed"}],
        "evidence": [{"id": "E1", "key": "e1", "passed": True, "content": {"p95Ms": 117}}],
        "evidence_gate": {"E1": True},
        "facts": {"F_INDEX_MISSING": True, "F_ENDPOINT_DEGRADED": True},
        "policy": {"scn001": "supported", "scn002": "unknown"},
        "root_cause_code": None,
        "confirmed_hypothesis_id": None,
        "termination_reason": None,
        "lock_evidence_refresh_count": 0,
        "tool_call_count": 3, "decision_attempt_count": 2,
        "_internal": {"secret": "不应出现"},
    }
    base.update(over)
    return base


def test_snapshot_whitelist_filters_internal_fields():
    snap = ReplaySnapshotFactory().snapshot(_state())
    assert "_internal" not in snap
    assert "tool_call_count" not in snap  # 白名单外
    assert snap["facts"]["F_INDEX_MISSING"] is True
    assert snap["diagnostic_policies"]["scn001"] == "supported"


def test_snapshot_is_deep_copy():
    state = _state()
    snap = ReplaySnapshotFactory().snapshot(state)
    state["facts"]["F_INDEX_MISSING"] = False  # 改原 state
    assert snap["facts"]["F_INDEX_MISSING"] is True  # 快照不受影响


def test_canonical_json_and_hash_stable():
    a = {"b": 2, "a": [1, {"d": 4, "c": 3}]}
    b = {"a": [1, {"c": 3, "d": 4}], "b": 2}
    assert canonical_json(a) == canonical_json(b)
    assert snapshot_hash(a) == snapshot_hash(b)
    assert len(snapshot_hash(a)) == 64


def test_exclusion_conditions_separated():
    snap = ReplaySnapshotFactory().snapshot(_state(facts={"F_INDEX_MISSING": False}))
    assert snap["exclusion_conditions"]["X_INDEX_NORMAL"] is True
