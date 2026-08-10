package com.tracemind.inventory.scenario;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;

@RestController
@RequestMapping("/internal/scenarios/SCN-001")
public class ScenarioController {
    private final ScenarioService scenarioService;
    private final ScenarioAuditMapper auditMapper;
    private final boolean demoMode;
    private final String demoKey;

    public ScenarioController(ScenarioService scenarioService, ScenarioAuditMapper auditMapper,
                              @Value("${DEMO_MODE:false}") boolean demoMode,
                              @Value("${DEMO_KEY:}") String demoKey) {
        this.scenarioService = scenarioService;
        this.auditMapper = auditMapper;
        this.demoMode = demoMode;
        this.demoKey = demoKey;
    }

    @PostMapping("/inject")
    public ResponseEntity<?> inject(@RequestHeader(value = "x-demo-key", required = false) String key) {
        if (!demoMode) {
            return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        }
        if (!demoKey.equals(key)) {
            return ResponseEntity.status(401).body(Map.of("error", "invalid demo key"));
        }
        ScenarioService.InjectResult result = scenarioService.inject();
        audit("inject", key);
        return ResponseEntity.ok(result);
    }

    @PostMapping("/reset")
    public ResponseEntity<?> reset(@RequestHeader(value = "x-demo-key", required = false) String key) {
        if (!demoMode) {
            return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        }
        if (!demoKey.equals(key)) {
            return ResponseEntity.status(401).body(Map.of("error", "invalid demo key"));
        }
        ScenarioService.ResetResult result = scenarioService.reset();
        audit("reset", key);
        return ResponseEntity.ok(result);
    }

    @GetMapping("/status")
    public ResponseEntity<?> status() {
        if (!demoMode) {
            return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        }
        return ResponseEntity.ok(scenarioService.status());
    }

    private void audit(String action, String actor) {
        ScenarioAudit a = new ScenarioAudit();
        a.setScenarioId("SCN-001");
        a.setAction(action);
        a.setActor(actor == null ? "unknown" : actor);
        a.setDetail("{\"at\":\"" + Instant.now() + "\"}");
        auditMapper.insert(a);
    }
}
