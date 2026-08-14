package com.tracemind.inventory.scenario;

import com.tracemind.common.obs.ObservationStore;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * 场景控制(SCN-001 缺索引 / SCN-002 锁阻塞)。
 * 锁持有:后台连接 autocommit=false → SELECT ... FOR UPDATE 持有目标库存行锁并保持;
 * reset 用 ROLLBACK + close(不改业务数据),幂等。
 */
@Service
public class ScenarioService {
    private static final String INDEX_EXISTS_SQL =
            "SELECT COUNT(*) FROM information_schema.statistics " +
            "WHERE table_schema = DATABASE() AND table_name = 'inventory' " +
            "AND index_name = 'idx_sku_warehouse'";
    private static final long LOCK_SKU = 42L;
    private static final long LOCK_WAREHOUSE = 7L;

    private final JdbcTemplate jdbcTemplate;
    private final ObservationStore observationStore;
    private final ExecutorService lockExecutor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "scenario-lock-holder");
        t.setDaemon(true);
        return t;
    });
    private volatile Future<?> lockTask;
    private volatile Connection lockConnection;
    private volatile boolean lockHeld;

    public ScenarioService(JdbcTemplate jdbcTemplate, ObservationStore observationStore) {
        this.jdbcTemplate = jdbcTemplate;
        this.observationStore = observationStore;
    }

    public InjectResult inject(String scenario) {
        if ("SCN-001".equals(scenario)) {
            return injectIndexFault();
        }
        if ("SCN-002".equals(scenario)) {
            if (indexInjected()) {
                return new InjectResult("CONFLICT", "scn001_already_injected");  // 场景互斥
            }
            return injectLockFault();
        }
        return new InjectResult("UNKNOWN_SCENARIO", scenario);
    }

    private void ensureLockTarget() {
        Integer cnt = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM inventory WHERE sku_id=? AND warehouse_id=?",
                Integer.class, LOCK_SKU, LOCK_WAREHOUSE);
        if (cnt == null || cnt == 0) {
            jdbcTemplate.update("INSERT INTO inventory (sku_id, warehouse_id, quantity) VALUES (?, ?, ?)",
                                LOCK_SKU, LOCK_WAREHOUSE, 100);
        }
    }

    private boolean indexInjected() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        return present == null || present == 0;
    }

    private InjectResult injectIndexFault() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        if (present == null || present == 0) {
            return new InjectResult("FAULTY", "already_faulty");   // 幂等
        }
        jdbcTemplate.execute("DROP INDEX idx_sku_warehouse ON inventory");
        observationStore.clear();
        return new InjectResult("FAULTY", "injected");
    }

    private InjectResult injectLockFault() {
        if (lockHeld) {
            return new InjectResult("FAULTY", "lock_already_held");   // 幂等
        }
        try {
            DataSource ds = jdbcTemplate.getDataSource();
            if (ds == null) {
                return new InjectResult("FAULTY", "no_datasource");
            }
            ensureLockTarget();   // 保证 42/7 记录存在(不存在则插入),幂等
            observationStore.clear();   // 清除健康样本,故障指标不被稀释
            Connection conn = ds.getConnection();
            conn.setAutoCommit(false);
            lockConnection = conn;
            final CountDownLatch lockAcquired = new CountDownLatch(1);
            lockTask = lockExecutor.submit(() -> {
                try (PreparedStatement ps = conn.prepareStatement(
                        "SELECT id FROM inventory WHERE sku_id=? AND warehouse_id=? FOR UPDATE")) {
                    ps.setLong(1, LOCK_SKU);
                    ps.setLong(2, LOCK_WAREHOUSE);
                    if (!ps.executeQuery().next()) {
                        throw new IllegalStateException("FOR UPDATE 未命中任何记录");
                    }
                    // 锁已真正持有:通知主线程(同步等锁,避免 inject 返回后负载先拿到锁形成反向锁等待链)
                    lockAcquired.countDown();
                    // 保持连接与事务不结束,直到 reset
                    while (!Thread.currentThread().isInterrupted()) {
                        Thread.sleep(1000);
                    }
                } catch (InterruptedException ignored) {
                    // 被 reset 中断
                } catch (Exception e) {
                    // 持锁失败:回滚并清理
                    try { conn.rollback(); } catch (Exception ignored) {}
                    try { conn.close(); } catch (Exception ignored) {}
                    lockHeld = false;
                    lockConnection = null;
                } finally {
                    lockAcquired.countDown();   // 失败也放行,主线程以 lockHeld 判定
                    try { conn.rollback(); } catch (Exception ignored) {}
                    try { conn.close(); } catch (Exception ignored) {}
                    lockHeld = false;
                    lockConnection = null;
                }
            });
            // 同步等待锁真正持有(executeQuery 成功)或失败;最多 5s,超时视为注入失败
            boolean acquired = lockAcquired.await(5, TimeUnit.SECONDS);
            lockHeld = acquired;
            if (!acquired) {
                lockTask.cancel(true);
                return new InjectResult("FAULTY", "lock_acquire_timeout");
            }
            return new InjectResult("FAULTY", "lock_injected");
        } catch (Exception e) {
            lockHeld = false;
            lockConnection = null;
            return new InjectResult("FAULTY", "lock_inject_failed:" + e.getMessage());
        }
    }

    public ResetResult reset(String scenario) {
        if ("SCN-002".equals(scenario) || scenario == null || scenario.isBlank()) {
            resetLockFault();   // ROLLBACK + close + 清理;连接已断开也返回幂等成功
        }
        if ("SCN-001".equals(scenario) || scenario == null || scenario.isBlank()) {
            resetIndexIfNeeded();
        }
        return new ResetResult("HEALTHY", "reset");
    }

    private void resetLockFault() {
        Future<?> task = lockTask;
        if (task != null) {
            task.cancel(true);
            try {
                task.get(3, TimeUnit.SECONDS);   // 等待持锁线程中断并回滚关闭
            } catch (Exception ignored) {
                // 线程未及时退出:连接由 finally 兜底关闭
            }
        }
        lockTask = null;
        lockHeld = false;
        lockConnection = null;
    }

    private void resetIndexIfNeeded() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        if (present == null || present == 0) {
            jdbcTemplate.execute("CREATE INDEX idx_sku_warehouse ON inventory (sku_id, warehouse_id)");
        }
        observationStore.clear();
    }

    public ScenarioStatus status() {
        Integer present = jdbcTemplate.queryForObject(INDEX_EXISTS_SQL, Integer.class);
        boolean indexPresent = present != null && present > 0;
        String active = lockHeld ? "SCN-002" : (!indexPresent ? "SCN-001" : null);
        return new ScenarioStatus(indexPresent, lockHeld, active);
    }

    public record InjectResult(String status, String detail) {
    }

    public record ResetResult(String status, String detail) {
    }

    public record ScenarioStatus(boolean indexPresent, boolean lockHeld, String activeScenario) {
    }
}
