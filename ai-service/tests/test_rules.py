from app.agent.rules import evaluate_evidence_gate, evaluate_recovery_rule


def test_gate_requires_all_e1_to_e5():
    evidence = {"E1": True, "E2": True, "E3": True, "E4": True, "E5": False}
    assert evaluate_evidence_gate(evidence) is False


def test_gate_passes_when_all_met():
    evidence = {f"E{i}": True for i in range(1, 6)}
    assert evaluate_evidence_gate(evidence) is True


def test_recovery_rule_needs_three_probes():
    probes = [{"p95_ms": 100}, {"p95_ms": 110}]
    assert evaluate_recovery_rule(probes, baseline_p95=100) is False


def test_recovery_rule_all_below_threshold():
    probes = [{"p95_ms": 100}, {"p95_ms": 110}, {"p95_ms": 115}]
    assert evaluate_recovery_rule(probes, baseline_p95=100, threshold_ratio=1.2) is True


def test_recovery_rule_fails_on_high_p95():
    probes = [{"p95_ms": 100}, {"p95_ms": 110}, {"p95_ms": 130}]
    assert evaluate_recovery_rule(probes, baseline_p95=100, threshold_ratio=1.2) is False
