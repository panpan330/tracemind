package com.tracemind.inventory.scenario;

import com.tracemind.common.obs.ObservationStore;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class ScenarioService {
    private static final String INDEX_EXISTS_SQL =
            "SELECT COUNT(*) FROM information_schema.statistics " +
            "WHERE table_schema = DATABASE() AND table_name = 'inventory' " +
            "AND index_name = 'idx_sku_warehouse'";

    private final JdbcTemplate jdbcTemplate;
    private final ObservationStore observationStore;

    public ScenarioService(JdbcTemplate jdbcTemplate, ObservationStore observationStore) {
        this.jdbcTemplate = jdbcTemplate;
        this.observationStore = observationStore;
    }

    public InjectResult inject() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        if (present == null || present == 0) {
            return new InjectResult("FAULTY", "already_faulty");   // 幂等
        }
        jdbcTemplate.execute("DROP INDEX idx_sku_warehouse ON inventory");
        observationStore.clear();
        return new InjectResult("FAULTY", "injected");
    }

    public ResetResult reset() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        if (present == null || present == 0) {
            jdbcTemplate.execute("CREATE INDEX idx_sku_warehouse ON inventory (sku_id, warehouse_id)");
        }
        observationStore.clear();
        return new ResetResult("HEALTHY", "reset");
    }

    public ScenarioStatus status() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        return new ScenarioStatus(present != null && present > 0);
    }

    public record InjectResult(String status, String detail) {
    }

    public record ResetResult(String status, String detail) {
    }

    public record ScenarioStatus(boolean indexPresent) {
    }
}
