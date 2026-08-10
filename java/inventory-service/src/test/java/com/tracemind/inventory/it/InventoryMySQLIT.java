package com.tracemind.inventory.it;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.service.InventoryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 真实 MySQL 集成测试(Testcontainers)。
 * 需要 Docker 环境:默认随 failsafe 在 verify 阶段执行;
 * 本机无 Docker 时跳过,或配置 DOCKER_HOST 指向远程 Docker。
 */
@Testcontainers
@SpringBootTest
class InventoryMySQLIT {

    @Container
    static MySQLContainer<?> mysql = new MySQLContainer<>("mysql:8.0")
            .withDatabaseName("tracemind_business")
            .withUsername("app_business")
            .withPassword("app_business_pwd")
            .withInitScript("init-schema.sql");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", mysql::getJdbcUrl);
        registry.add("spring.datasource.username", mysql::getUsername);
        registry.add("spring.datasource.password", mysql::getPassword);
    }

    @Autowired
    private InventoryService inventoryService;
    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Test
    void lookupReturnsRowAndUsesCompositeIndex() {
        Optional<Inventory> hit = inventoryService.queryStock(42L, 7L);
        assertThat(hit).isPresent();
        assertThat(hit.get().getQuantity()).isEqualTo(100);

        // 执行计划必须命中 idx_sku_warehouse(ref),而非全表扫描(ALL)
        List<Map<String, Object>> plan = jdbcTemplate.queryForList(
                "EXPLAIN SELECT * FROM inventory WHERE sku_id = 42 AND warehouse_id = 7");
        assertThat(plan.get(0).get("type").toString()).isEqualTo("ref");
        assertThat(plan.get(0).get("key").toString()).isEqualTo("idx_sku_warehouse");
    }
}
