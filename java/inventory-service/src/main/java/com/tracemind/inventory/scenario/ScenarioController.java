package com.tracemind.inventory.scenario;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;

@RestController
@RequestMapping("/internal/scenarios")
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

    @PostMapping("/{scenario}/{action}")
    public ResponseEntity<?> scenario(@PathVariable String scenario,
                                      @PathVariable String action,
                                      @RequestHeader(value = "x-demo-key", required = false) String key) {
        if (!demoMode) {
            return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        }
        if (!demoKey.equals(key)) {
            return ResponseEntity.status(401).body(Map.of("error", "invalid demo key"));
        }
        if ("inject".equals(action)) {
            ScenarioService.InjectResult result = scenarioService.inject(scenario);
            if ("CONFLICT".equals(result.status())) {
                return ResponseEntity.status(409).body(result);   // 场景互斥
            }
            audit(scenario, "inject", key);
            return ResponseEntity.ok(result);
        }
        if ("reset".equals(action)) {
            ScenarioService.ResetResult result = scenarioService.reset(scenario);
            audit(scenario, "reset", key);
            return ResponseEntity.ok(result);
        }
        return ResponseEntity.badRequest().body(Map.of("error", "unknown action: " + action));
    }

    @GetMapping("/status")
    public ResponseEntity<?> status() {
        if (!demoMode) {
            return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        }
        return ResponseEntity.ok(scenarioService.status());
    }

    private void audit(String scenario, String action, String actor) {
        ScenarioAudit a = new ScenarioAudit();
        a.setScenarioId(scenario);
        a.setAction(action);
        a.setActor(actor == null ? "unknown" : actor);
        a.setDetail("{\"at\":\"" + Instant.now() + "\"}");
        auditMapper.insert(a);
    }
}
