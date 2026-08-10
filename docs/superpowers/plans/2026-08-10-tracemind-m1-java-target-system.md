# TraceMind M1:Java 故障目标系统 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建可重复注入/恢复"缺联合索引"故障、暴露真实指标与 traceId 关联的 Java 目标系统(order-service + inventory-service + 真实 MySQL),使"有索引 vs 无索引的执行计划差异"稳定可见。

**Architecture:** Maven 多模块仓库(`common` 共享库 + 两个独立 Spring Boot 服务)。order-service 通过 HTTP 调用 inventory-service 查询库存,请求携带 `traceId`(MDC + `x-trace-id` 响应头);两个服务各自用内存 Ring Buffer 记录阶段耗时并暴露内部观测端点;inventory-service 提供 SCN-001 场景控制(仅 `DEMO_MODE=true` + 管理密钥)。数据由 Python 脚本灌入真实 MySQL。

**Tech Stack:** Java 21、Spring Boot 3.3.x、MyBatis-Plus 3.5.x、Maven、MySQL 8、JUnit 5 + Mockito、Python 3.12(数据生成/负载脚本)。

## Global Constraints

以下约束来自设计文档,所有任务默认包含(逐字):

- 目标查询:`SELECT ... FROM inventory WHERE sku_id = ? AND warehouse_id = ?`
- 目标联合索引:`idx_sku_warehouse(sku_id, warehouse_id)` —— 全文统一,不得出现第二种写法
- 端口:order-service=8081、inventory-service=8082
- 数据库账号四枚:`app_business`(业务读写)/ `tracemind_control_app`(控制库 CRUD,本期仅建库授权)/ `ai_investigator`(只读业务 + performance_schema)/ `fix_executor`(仅目标表 INDEX 权限)
- `traceId`:MDC + 响应头 `x-trace-id`;观测记录 TTL 10 分钟、上限 10,000 条;查无返回 `TRACE_NOT_FOUND`
- 观测端点:内部接口返回固定结构,不暴露 Actuator 原始响应;窗口内最慢请求作为 `representative_slow_trace_id`
- 场景控制:`POST /internal/scenarios/SCN-001/inject|reset`、`GET /internal/scenarios/SCN-001/status`;仅 `DEMO_MODE=true` 启用;管理密钥仅存环境变量 `DEMO_KEY`,经请求头 `x-demo-key` 传递;注入/重置幂等且写审计
- 所有地址走环境变量,不写死 localhost:`BUSINESS_DB_URL`、`CONTROL_DB_URL`、`INVENTORY_SERVICE_URL`、`ORDER_SERVICE_URL`、`AI_SERVICE_URL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`、`LLM_MODE`、`CHECKPOINT_DB_PATH`、`DEMO_MODE`、`DEMO_KEY`
- 数据量环境变量 `INVENTORY_ROWS` 控制(默认 500,000)
- 测试库:`tracemind_business_test`(M1 测试用本地 MySQL,Testcontainers 属 M5)

## File Structure

```
java/
  pom.xml                                  # 父 pom:modules common/order-service/inventory-service
  common/
    pom.xml
    src/main/java/com/tracemind/common/trace/TraceIdFilter.java
    src/main/java/com/tracemind/common/obs/ObservationRecord.java
    src/main/java/com/tracemind/common/obs/ObservationStore.java
    src/main/java/com/tracemind/common/obs/ObservationController.java
    src/main/java/com/tracemind/common/obs/MetricsCollector.java
  order-service/
    pom.xml
    src/main/java/com/tracemind/order/OrderServiceApplication.java
    src/main/java/com/tracemind/order/controller/OrderController.java
    src/main/java/com/tracemind/order/service/OrderService.java
    src/main/java/com/tracemind/order/client/InventoryClient.java
    src/main/java/com/tracemind/order/OrderTraceInterceptor.java
    src/main/resources/application.yml
    src/test/java/com/tracemind/order/... (测试)
  inventory-service/
    pom.xml
    src/main/java/com/tracemind/inventory/InventoryServiceApplication.java
    src/main/java/com/tracemind/inventory/controller/InventoryController.java
    src/main/java/com/tracemind/inventory/entity/Inventory.java
    src/main/java/com/tracemind/inventory/mapper/InventoryMapper.java
    src/main/java/com/tracemind/inventory/service/InventoryService.java
    src/main/java/com/tracemind/inventory/scenario/ScenarioController.java
    src/main/java/com/tracemind/inventory/scenario/ScenarioService.java
    src/main/java/com/tracemind/inventory/scenario/ScenarioAuditMapper.java
    src/main/resources/application.yml
    src/test/java/com/tracemind/inventory/... (测试)
scripts/
  init-database.ps1        # 建 schema/账号/授权/DDL(幂等)
  seed_data.py             # 灌 inventory 数据(批量 insert)
  generate-data.ps1        # 封装 seed_data.py
  loadgen.py               # 负载发生器(循环调 order 接口)
  run-load.ps1             # 封装 loadgen.py
  verify-m1.ps1            # M1 验收:EXPLAIN 对比 + P95 对比
```

所有 Spring Boot 主类使用 `@SpringBootApplication(scanBasePackages = "com.tracemind")`,使 common 中的 Bean/Controller 被两个服务扫描。

---

### Task 1.1: Maven 多模块骨架 + 两个可启动服务

**Files:**
- Create: `java/pom.xml`
- Create: `java/common/pom.xml`
- Create: `java/order-service/pom.xml`
- Create: `java/inventory-service/pom.xml`
- Create: `java/order-service/src/main/java/com/tracemind/order/OrderServiceApplication.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/InventoryServiceApplication.java`
- Create: `java/order-service/src/main/resources/application.yml`
- Create: `java/inventory-service/src/main/resources/application.yml`
- Test: `java/order-service/src/test/java/com/tracemind/order/OrderServiceApplicationTest.java`
- Test: `java/inventory-service/src/test/java/com/tracemind/inventory/InventoryServiceApplicationTest.java`

**Interfaces:**
- Produces: 可 `mvn -pl order-service spring-boot:run` 启动的服务(端口 8081/8082,`GET /actuator/health` 返回 UP);common 模块被两个服务依赖。

- [ ] **Step 1: 写父 pom.xml**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.tracemind</groupId>
  <artifactId>tracemind-java</artifactId>
  <version>0.1.0-SNAPSHOT</version>
  <packaging>pom</packaging>
  <modules>
    <module>common</module>
    <module>order-service</module>
    <module>inventory-service</module>
  </modules>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.5</version>
    <relativePath/>
  </parent>
  <properties>
    <java.version>21</java.version>
    <mybatis-plus.version>3.5.7</mybatis-plus.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.baomidou</groupId>
        <artifactId>mybatis-plus-spring-boot3-starter</artifactId>
        <version>${mybatis-plus.version}</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
```

- [ ] **Step 2: 写 common、order-service、inventory-service 的 pom.xml**

`java/common/pom.xml`:

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>com.tracemind</groupId>
    <artifactId>tracemind-java</artifactId>
    <version>0.1.0-SNAPSHOT</version>
  </parent>
  <artifactId>common</artifactId>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-actuator</artifactId>
    </dependency>
  </dependencies>
</project>
```

`java/order-service/pom.xml` 与 `java/inventory-service/pom.xml`(内容相同,除 artifactId):

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>com.tracemind</groupId>
    <artifactId>tracemind-java</artifactId>
    <version>0.1.0-SNAPSHOT</version>
  </parent>
  <artifactId>order-service</artifactId>
  <dependencies>
    <dependency><groupId>com.tracemind</groupId><artifactId>common</artifactId><version>${project.version}</version></dependency>
    <dependency>
      <groupId>com.baomidou</groupId>
      <artifactId>mybatis-plus-spring-boot3-starter</artifactId>
    </dependency>
    <dependency>
      <groupId>com.mysql</groupId>
      <artifactId>mysql-connector-j</artifactId>
      <scope>runtime</scope>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
```

- [ ] **Step 3: 写两个主类与 application.yml**

`OrderServiceApplication.java`:

```java
package com.tracemind.order;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.tracemind")
public class OrderServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderServiceApplication.class, args);
    }
}
```

`InventoryServiceApplication.java` 同构(包名 `com.tracemind.inventory`)。

`java/order-service/src/main/resources/application.yml`:

```yaml
server:
  port: ${ORDER_SERVICE_PORT:8081}
spring:
  application:
    name: order-service
  datasource:
    url: ${BUSINESS_DB_URL:jdbc:mysql://localhost:3306/tracemind_business?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true}
    username: ${BUSINESS_DB_USER:app_business}
    password: ${BUSINESS_DB_PASSWORD:app_business_pwd}
management:
  endpoints:
    web:
      exposure:
        include: health,metrics
```

`java/inventory-service/src/main/resources/application.yml` 同构,端口 `${INVENTORY_SERVICE_PORT:8082}`。

- [ ] **Step 4: 写两个 contextLoads 测试**

`OrderServiceApplicationTest.java`:

```java
package com.tracemind.order;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class OrderServiceApplicationTest {
    @Test
    void contextLoads() {
    }
}
```

`InventoryServiceApplicationTest.java` 同构。测试使用 `application.yml` 中的默认数据源(本地 MySQL 需先执行 Task 1.2 的 `init-database.ps1`)。

- [ ] **Step 5: 运行测试验证**

Run: `cd java && mvn -q test`
Expected: BUILD SUCCESS,两个 contextLoads 测试通过。若本地 MySQL 未初始化,先执行 Task 1.2。

- [ ] **Step 6: 提交**

```bash
git add java/
git commit -m "feat(java): Maven 多模块骨架,order/inventory 服务可启动"
```

---

### Task 1.2: 数据库初始化(双 Schema + 四账号 + 业务 DDL)

**Files:**
- Create: `scripts/init-database.ps1`
- Test: 手动执行脚本 + SQL 验证(见 Step 3)

**Interfaces:**
- Produces: 库 `tracemind_business`、`tracemind_business_test`、`tracemind_control`;账号 `app_business`/`tracemind_control_app`/`ai_investigator`/`fix_executor`;表 `inventory`(含 `idx_sku_warehouse`)、`orders`、`order_item`、`scenario_audit`。Task 1.3 依赖 `inventory` 表,Task 1.8 依赖 `scenario_audit` 表。

- [ ] **Step 1: 写 init-database.ps1**

脚本要求:可重复执行(幂等),从环境变量读 root 密码。开头:

```powershell
param(
  [string]$RootPassword = $env:MYSQL_ROOT_PASSWORD,
  [string]$Host = "localhost",
  [int]$Port = 3306
)
if (-not $RootPassword) { throw "请设置 MYSQL_ROOT_PASSWORD 环境变量" }
$mysql = "mysql -h $Host -P $Port -uroot -p$RootPassword"
```

然后用 `Invoke-Expression "$mysql -e `"CREATE DATABASE IF NOT EXISTS tracemind_business ...`""` 依次执行(UTF8MB4):

```sql
CREATE DATABASE IF NOT EXISTS tracemind_business DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS tracemind_business_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS tracemind_control DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'app_business'@'%' IDENTIFIED BY 'app_business_pwd';
CREATE USER IF NOT EXISTS 'tracemind_control_app'@'%' IDENTIFIED BY 'control_app_pwd';
CREATE USER IF NOT EXISTS 'ai_investigator'@'%' IDENTIFIED BY 'investigator_pwd';
CREATE USER IF NOT EXISTS 'fix_executor'@'%' IDENTIFIED BY 'fix_executor_pwd';

GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business.* TO 'app_business'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_business_test.* TO 'app_business'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON tracemind_control.* TO 'tracemind_control_app'@'%';
GRANT SELECT ON tracemind_business.* TO 'ai_investigator'@'%';
GRANT SELECT ON tracemind_business_test.* TO 'ai_investigator'@'%';
GRANT SELECT ON performance_schema.* TO 'ai_investigator'@'%';
GRANT INDEX ON tracemind_business.inventory TO 'fix_executor'@'%';
FLUSH PRIVILEGES;
```

业务 DDL(幂等,`CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS inventory (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  sku_id BIGINT NOT NULL,
  warehouse_id BIGINT NOT NULL,
  quantity INT NOT NULL DEFAULT 0,
  version INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_sku_warehouse (sku_id, warehouse_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS orders (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_no VARCHAR(32) NOT NULL UNIQUE,
  customer_id BIGINT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'CREATED',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS order_item (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT NOT NULL,
  sku_id BIGINT NOT NULL,
  warehouse_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scenario_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  scenario_id VARCHAR(32) NOT NULL,
  action VARCHAR(16) NOT NULL,
  actor VARCHAR(64) NOT NULL,
  detail JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
```

提示:`ai_investigator` 无需授权 `information_schema`(所有用户只读可见);脚本末尾打印"数据库初始化完成"。

- [ ] **Step 2: 执行脚本**

Run: `powershell -ExecutionPolicy Bypass -File scripts/init-database.ps1`(先 `$env:MYSQL_ROOT_PASSWORD=...`)
Expected: 脚本无报错,打印完成。

- [ ] **Step 3: 验证账号与权限**

Run(用 root):

```bash
mysql -uroot -p$MYSQL_ROOT_PASSWORD -e "SHOW INDEX FROM tracemind_business.inventory;"
mysql -uai_investigator -pinvestigator_pwd -e "SELECT COUNT(*) FROM tracemind_business.inventory;" 2>&1   # 应报"表为空"或返回 0,而不是权限错误
mysql -uai_investigator -pinvestigator_pwd -e "DELETE FROM tracemind_business.inventory;" 2>&1            # 应报权限拒绝
```

Expected: 索引 `idx_sku_warehouse` 存在;investigator 可 SELECT、不可 DELETE。

- [ ] **Step 4: 提交**

```bash
git add scripts/init-database.ps1
git commit -m "feat(db): 初始化双 Schema、四账号权限与业务 DDL(幂等)"
```

---

### Task 1.3: 库存数据生成

**Files:**
- Create: `scripts/seed_data.py`
- Create: `scripts/generate-data.ps1`
- Test: 手动执行 + SQL 校验(见 Step 3)

**Interfaces:**
- Produces: `inventory` 表填充 `INVENTORY_ROWS` 行(默认 500,000),`sku_id ∈ [0,20000)`、`warehouse_id ∈ [0,50)` 随机组合,`quantity ∈ [0,1000)`。Task 1.4 的查询依赖此数据。

- [ ] **Step 1: 写 seed_data.py**

```python
"""灌入 inventory 压测数据。用法: python scripts/seed_data.py"""
import os
import random
import sys

import pymysql

DB_URL = os.environ.get("BUSINESS_DB_URL", "localhost:3306/tracemind_business")
HOST, rest = DB_URL.split(":", 1)
PORT, DB = rest.split("/", 1)
USER = os.environ.get("BUSINESS_DB_USER", "app_business")
PASSWORD = os.environ.get("BUSINESS_DB_PASSWORD", "app_business_pwd")
ROWS = int(os.environ.get("INVENTORY_ROWS", "500000"))
BATCH = 5000


def main() -> None:
    conn = pymysql.connect(host=HOST, port=int(PORT), user=USER, password=PASSWORD, database=DB, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM inventory")  # 先清空,保证可重复执行
            random.seed(42)
            inserted = 0
            while inserted < ROWS:
                batch = [
                    (random.randint(0, 19999), random.randint(0, 49), random.randint(0, 999))
                    for _ in range(min(BATCH, ROWS - inserted))
                ]
                cur.executemany(
                    "INSERT INTO inventory (sku_id, warehouse_id, quantity) VALUES (%s, %s, %s)", batch
                )
                conn.commit()
                inserted += len(batch)
                print(f"inserted {inserted}/{ROWS}")
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 写 generate-data.ps1**

```powershell
param([int]$Rows = 500000)
$env:INVENTORY_ROWS = "$Rows"
python scripts/seed_data.py
```

- [ ] **Step 3: 执行并校验**

Run: `powershell -ExecutionPolicy Bypass -File scripts/generate-data.ps1 -Rows 500000`
Run:

```bash
mysql -uapp_business -papp_business_pwd tracemind_business -e "SELECT COUNT(*), COUNT(DISTINCT sku_id), COUNT(DISTINCT warehouse_id) FROM inventory;"
```

Expected: `500000` 行;两次执行后仍为 500000(幂等)。

- [ ] **Step 4: 提交**

```bash
git add scripts/seed_data.py scripts/generate-data.ps1
git commit -m "feat(scripts): inventory 压测数据生成(幂等,INVENTORY_ROWS 可配)"
```

---

### Task 1.4: inventory-service 业务查询(目标 SQL)

**Files:**
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/entity/Inventory.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/mapper/InventoryMapper.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/service/InventoryService.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/controller/InventoryController.java`
- Test: `java/inventory-service/src/test/java/com/tracemind/inventory/service/InventoryServiceTest.java`

**Interfaces:**
- Produces: `GET /api/inventory?skuId={skuId}&warehouseId={warehouseId}` → `200 {"skuId":..., "warehouseId":..., "quantity":...}`;`InventoryService.queryStock(skuId: long, warehouseId: long): Optional<Inventory>`;SQL 为 `SELECT ... FROM inventory WHERE sku_id = ? AND warehouse_id = ?`(走 `idx_sku_warehouse`)。Task 1.5 的 order-service 调用此端点。

- [ ] **Step 1: 写失败测试(Service 层)**

`InventoryServiceTest.java`:

```java
package com.tracemind.inventory.service;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.mapper.InventoryMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class InventoryServiceTest {
    @Mock InventoryMapper inventoryMapper;
    @InjectMocks InventoryService inventoryService;

    @Test
    void queryStock_returnsInventory_whenFound() {
        when(inventoryMapper.selectBySkuAndWarehouse(42L, 7L)).thenReturn(
            new Inventory(1L, 42L, 7L, 100, 0));
        Optional<Inventory> result = inventoryService.queryStock(42L, 7L);
        assertThat(result).isPresent();
        assertThat(result.get().getQuantity()).isEqualTo(100);
    }

    @Test
    void queryStock_returnsEmpty_whenNotFound() {
        when(inventoryMapper.selectBySkuAndWarehouse(999L, 999L)).thenReturn(null);
        assertThat(inventoryService.queryStock(999L, 999L)).isEmpty();
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd java && mvn -pl inventory-service -q test -Dtest=InventoryServiceTest`
Expected: FAIL — `InventoryService`/`InventoryMapper` 不存在(编译失败)。

- [ ] **Step 3: 写实体、Mapper、Service、Controller**

`Inventory.java`:

```java
package com.tracemind.inventory.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("inventory")
public class Inventory {
    @TableId(type = IdType.AUTO)
    private Long id;
    private Long skuId;
    private Long warehouseId;
    private Integer quantity;
    private Integer version;

    public Inventory() {}
    public Inventory(Long id, Long skuId, Long warehouseId, Integer quantity, Integer version) {
        this.id = id; this.skuId = skuId; this.warehouseId = warehouseId;
        this.quantity = quantity; this.version = version;
    }
    // getters/setters(标准生成)
}
```

`InventoryMapper.java`:

```java
package com.tracemind.inventory.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.tracemind.inventory.entity.Inventory;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface InventoryMapper extends BaseMapper<Inventory> {
    @Select("SELECT id, sku_id, warehouse_id, quantity, version FROM inventory " +
            "WHERE sku_id = #{skuId} AND warehouse_id = #{warehouseId}")
    Inventory selectBySkuAndWarehouse(@Param("skuId") long skuId,
                                      @Param("warehouseId") long warehouseId);
}
```

`InventoryService.java`:

```java
package com.tracemind.inventory.service;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.mapper.InventoryMapper;
import org.springframework.stereotype.Service;

import java.util.Optional;

@Service
public class InventoryService {
    private final InventoryMapper inventoryMapper;
    public InventoryService(InventoryMapper inventoryMapper) { this.inventoryMapper = inventoryMapper; }

    public Optional<Inventory> queryStock(long skuId, long warehouseId) {
        return Optional.ofNullable(inventoryMapper.selectBySkuAndWarehouse(skuId, warehouseId));
    }
}
```

`InventoryController.java`:

```java
package com.tracemind.inventory.controller;

import com.tracemind.inventory.entity.Inventory;
import com.tracemind.inventory.service.InventoryService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/inventory")
public class InventoryController {
    private final InventoryService inventoryService;
    public InventoryController(InventoryService inventoryService) { this.inventoryService = inventoryService; }

    @GetMapping
    public ResponseEntity<?> query(@RequestParam long skuId, @RequestParam long warehouseId) {
        return inventoryService.queryStock(skuId, warehouseId)
                .<ResponseEntity<?>>map(i -> ResponseEntity.ok(Map.of(
                        "skuId", i.getSkuId(), "warehouseId", i.getWarehouseId(), "quantity", i.getQuantity())))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd java && mvn -pl inventory-service -q test -Dtest=InventoryServiceTest`
Expected: PASS。

- [ ] **Step 5: 集成验证(真实 MySQL)**

启动服务:`cd java && mvn -pl inventory-service spring-boot:run`,然后:

```bash
curl "http://localhost:8082/api/inventory?skuId=42&warehouseId=7"
curl "http://localhost:8082/api/inventory?skuId=999999&warehouseId=999"
```

Expected: 前者 200 返回 quantity(若该组合不存在可改 skuId 重试),后者 404。

- [ ] **Step 6: 提交**

```bash
git add java/inventory-service
git commit -m "feat(inventory): 库存查询接口(目标 SQL WHERE sku_id AND warehouse_id)"
```

---

### Task 1.5: order-service 跨服务调用 + traceId 关联

**Files:**
- Create: `java/common/src/main/java/com/tracemind/common/trace/TraceIdFilter.java`
- Create: `java/order-service/src/main/java/com/tracemind/order/client/InventoryClient.java`
- Create: `java/order-service/src/main/java/com/tracemind/order/service/OrderService.java`
- Create: `java/order-service/src/main/java/com/tracemind/order/controller/OrderController.java`
- Test: `java/order-service/src/test/java/com/tracemind/order/trace/TraceIdFilterTest.java`

**Interfaces:**
- Consumes: `GET /api/inventory?skuId=&warehouseId=`(Task 1.4)。
- Produces: `POST /api/orders/{orderId}/check-stock {skuId, warehouseId, quantity}` → `200 {"sufficient": bool, "stock": int}`;`TraceIdFilter` 从请求头 `x-trace-id` 取值(无则生成 UUID),写入 MDC `traceId` 与响应头;order→inventory 的 HTTP 调用透传 `x-trace-id`。Task 1.6 依赖 MDC 中的 traceId 记录阶段耗时。

- [ ] **Step 1: 写失败测试(TraceIdFilter)**

`TraceIdFilterTest.java`(用 `MockHttpServletRequest/Response`):

```java
package com.tracemind.order.trace;

import com.tracemind.common.trace.TraceIdFilter;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class TraceIdFilterTest {
    @Test
    void generatesTraceId_whenHeaderAbsent() throws Exception {
        TraceIdFilter filter = new TraceIdFilter();
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/api/x");
        MockHttpServletResponse res = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);
        filter.doFilter(req, res, chain);
        assertThat(res.getHeader("x-trace-id")).isNotBlank();
        assertThat(MDC.get("traceId")).isEqualTo(res.getHeader("x-trace-id"));
    }

    @Test
    void propagatesTraceId_whenHeaderPresent() throws Exception {
        TraceIdFilter filter = new TraceIdFilter();
        MockHttpServletRequest req = new MockHttpServletRequest("GET", "/api/x");
        req.addHeader("x-trace-id", "trace-abc");
        MockHttpServletResponse res = new MockHttpServletResponse();
        filter.doFilter(req, res, mock(FilterChain.class));
        assertThat(res.getHeader("x-trace-id")).isEqualTo("trace-abc");
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd java && mvn -pl order-service -q test -Dtest=TraceIdFilterTest`
Expected: FAIL — `TraceIdFilter` 不存在。

- [ ] **Step 3: 写 TraceIdFilter**

`java/common/src/main/java/com/tracemind/common/trace/TraceIdFilter.java`:

```java
package com.tracemind.common.trace;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

@Component
public class TraceIdFilter extends OncePerRequestFilter {
    public static final String TRACE_ID_HEADER = "x-trace-id";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        String traceId = request.getHeader(TRACE_ID_HEADER);
        if (traceId == null || traceId.isBlank()) {
            traceId = UUID.randomUUID().toString();
        }
        MDC.put("traceId", traceId);
        response.setHeader(TRACE_ID_HEADER, traceId);
        try {
            filterChain.doFilter(request, response);
        } finally {
            MDC.remove("traceId");
        }
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd java && mvn -pl order-service -q test -Dtest=TraceIdFilterTest`
Expected: PASS。

- [ ] **Step 5: 写 InventoryClient、OrderService、OrderController**

`InventoryClient.java`(透传 `x-trace-id`,用 `RestClient`):

```java
package com.tracemind.order.client;

import com.tracemind.common.trace.TraceIdFilter;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

@Component
public class InventoryClient {
    private final RestClient restClient;

    public InventoryClient(@Value("${INVENTORY_SERVICE_URL:http://localhost:8082}") String baseUrl) {
        this.restClient = RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(new JdkClientHttpRequestFactory())
                .build();
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> queryStock(long skuId, long warehouseId) {
        return restClient.get()
                .uri(uriBuilder -> uriBuilder.path("/api/inventory")
                        .queryParam("skuId", skuId).queryParam("warehouseId", warehouseId).build())
                .header(TraceIdFilter.TRACE_ID_HEADER, MDC.get("traceId"))
                .retrieve()
                .body(Map.class);
    }
}
```

`OrderService.java`:

```java
package com.tracemind.order.service;

import com.tracemind.order.client.InventoryClient;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class OrderService {
    private final InventoryClient inventoryClient;
    public OrderService(InventoryClient inventoryClient) { this.inventoryClient = inventoryClient; }

    public boolean checkStock(long orderId, long skuId, long warehouseId, int quantity) {
        Map<String, Object> stock = inventoryClient.queryStock(skuId, warehouseId);
        int available = ((Number) stock.getOrDefault("quantity", 0)).intValue();
        return available >= quantity;
    }
}
```

`OrderController.java`:

```java
package com.tracemind.order.controller;

import com.tracemind.order.service.OrderService;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService orderService;
    public OrderController(OrderService orderService) { this.orderService = orderService; }

    @PostMapping("/{orderId}/check-stock")
    public Map<String, Object> checkStock(@PathVariable long orderId,
                                          @RequestBody Map<String, Object> body) {
        long skuId = ((Number) body.get("skuId")).longValue();
        long warehouseId = ((Number) body.get("warehouseId")).longValue();
        int quantity = ((Number) body.get("quantity")).intValue();
        boolean sufficient = orderService.checkStock(orderId, skuId, warehouseId, quantity);
        return Map.of("orderId", orderId, "sufficient", sufficient);
    }
}
```

- [ ] **Step 6: 集成验证**

启动两个服务后:

```bash
curl -s -X POST http://localhost:8081/api/orders/1/check-stock -H "Content-Type: application/json" -d '{"skuId":42,"warehouseId":7,"quantity":10}'
curl -s -D- -o /dev/null -H "x-trace-id: demo-trace-1" http://localhost:8082/api/inventory?skuId=42\&warehouseId=7
```

Expected: 前者返回 `{"orderId":1,"sufficient":true/false}`;后者响应头含 `x-trace-id: demo-trace-1`。

- [ ] **Step 7: 提交**

```bash
git add java/common java/order-service
git commit -m "feat(trace): TraceIdFilter 透传 + order 跨服务调用 inventory"
```

---

### Task 1.6: 阶段耗时观测记录(ObservationStore)

**Files:**
- Create: `java/common/src/main/java/com/tracemind/common/obs/ObservationRecord.java`
- Create: `java/common/src/main/java/com/tracemind/common/obs/ObservationStore.java`
- Create: `java/order-service/src/main/java/com/tracemind/order/OrderTraceInterceptor.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/InventoryTraceInterceptor.java`
- Create: `java/common/src/main/java/com/tracemind/common/obs/ObservationController.java`
- Test: `java/common/src/test/java/com/tracemind/common/obs/ObservationStoreTest.java`

**Interfaces:**
- Consumes: MDC `traceId`(Task 1.5)。
- Produces: `ObservationStore.record(service: String, traceId: String, stage: String, durationMs: long, success: boolean)`;`ObservationStore.get(traceId): List<ObservationRecord>`;`ObservationStore.recent(windowSeconds: int): List<ObservationRecord>`;`GET /internal/observations/traces/{traceId}` → 组合 order 与 inventory 两服务记录(跨服务由 AI 服务组合,本期各自返回本服务记录);查无 → `404 {"error":"TRACE_NOT_FOUND"}`。阶段名:`order.total`、`order.inventory_http`、`inventory.total`、`inventory.database`。

- [ ] **Step 1: 写失败测试(ObservationStore)**

`ObservationStoreTest.java`:

```java
package com.tracemind.common.obs;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class ObservationStoreTest {
    @Test
    void recordsAndRetrievesByTraceId() {
        ObservationStore store = new ObservationStore(10_000, 600_000);
        store.record("order-service", "t1", "order.total", 120, true);
        store.record("order-service", "t1", "order.inventory_http", 100, true);
        List<ObservationRecord> recs = store.get("t1");
        assertThat(recs).hasSize(2);
        assertThat(recs.get(0).traceId()).isEqualTo("t1");
    }

    @Test
    void evictsExpiredRecords() {
        ObservationStore store = new ObservationStore(10_000, 600_000);
        store.record("order-service", "old", "order.total", 1, true);
        store.advanceClockForTest(601_000); // 测试钩子:推进虚拟时钟
        assertThat(store.get("old")).isEmpty();
    }

    @Test
    void evictsOldestWhenFull() {
        ObservationStore store = new ObservationStore(2, 600_000);
        store.record("s", "a", "x", 1, true);
        store.record("s", "b", "x", 1, true);
        store.record("s", "c", "x", 1, true);
        assertThat(store.get("a")).isEmpty();
        assertThat(store.get("c")).hasSize(1);
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd java && mvn -pl common -q test -Dtest=ObservationStoreTest`
Expected: FAIL — 类不存在。

- [ ] **Step 3: 写 ObservationRecord 与 ObservationStore**

`ObservationRecord.java`(record 类型):

```java
package com.tracemind.common.obs;

public record ObservationRecord(
        String service, String traceId, String stage,
        long durationMs, boolean success, long occurredAtMillis) {
}
```

`ObservationStore.java`(并发安全的环形列表 + 虚拟时钟钩子):

```java
package com.tracemind.common.obs;

import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.function.Supplier;

@Component
public class ObservationStore {
    private final int maxRecords;
    private final long ttlMillis;
    private final ConcurrentLinkedDeque<ObservationRecord> records = new ConcurrentLinkedDeque<>();
    private Supplier<Long> clock = () -> Instant.now().toEpochMilli();

    public ObservationStore() {
        this(10_000, 600_000);
    }

    public ObservationStore(int maxRecords, long ttlMillis) {
        this.maxRecords = maxRecords;
        this.ttlMillis = ttlMillis;
    }

    public void record(String service, String traceId, String stage, long durationMs, boolean success) {
        long now = clock.get();
        records.addLast(new ObservationRecord(service, traceId, stage, durationMs, success, now));
        evict(now);
    }

    public List<ObservationRecord> get(String traceId) {
        long now = clock.get();
        return records.stream()
                .filter(r -> now - r.occurredAtMillis() <= ttlMillis)
                .filter(r -> r.traceId().equals(traceId))
                .toList();
    }

    public List<ObservationRecord> recent(long windowSeconds) {
        long now = clock.get();
        return records.stream()
                .filter(r -> now - r.occurredAtMillis() <= windowSeconds * 1000L)
                .toList();
    }

    public void clear() {
        records.clear();
    }

    void advanceClockForTest(long deltaMillis) {
        long base = clock.get();
        clock = () -> base + deltaMillis;
    }

    private void evict(long now) {
        while (records.size() > maxRecords) {
            records.pollFirst();
        }
        ObservationRecord first;
        while ((first = records.peekFirst()) != null && now - first.occurredAtMillis() > ttlMillis) {
            records.pollFirst();
        }
    }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd java && mvn -pl common -q test -Dtest=ObservationStoreTest`
Expected: PASS。

- [ ] **Step 5: 写两个服务各自的 TraceInterceptor**

`OrderTraceInterceptor.java`(common 提供抽象基类更优,此处两服务各自实现同构):

```java
package com.tracemind.order;

import com.tracemind.common.obs.ObservationStore;
import com.tracemind.common.trace.TraceIdFilter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
public class OrderTraceInterceptor extends OncePerRequestFilter {
    private final ObservationStore observationStore;
    public OrderTraceInterceptor(ObservationStore observationStore) { this.observationStore = observationStore; }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        if (request.getRequestURI().startsWith("/internal/")) {
            filterChain.doFilter(request, response);
            return;
        }
        long start = System.nanoTime();
        try {
            filterChain.doFilter(request, response);
        } finally {
            long totalMs = (System.nanoTime() - start) / 1_000_000;
            String traceId = MDC.get("traceId");
            if (traceId != null) {
                observationStore.record("order-service", traceId, "order.total", totalMs, response.getStatus() < 500);
            }
        }
    }
}
```

`InventoryTraceInterceptor.java` 同构(`inventory-service`、`inventory.total`);此外 inventory 需要记录 `inventory.database`(MyBatis 查询耗时):在 `InventoryService.queryStock` 中用 `System.nanoTime()` 包住 mapper 调用并 `observationStore.record("inventory-service", traceId, "inventory.database", dbMs, true)`,traceId 取 `MDC.get("traceId")`。

- [ ] **Step 6: 写 ObservationController(common)**

```java
package com.tracemind.common.obs;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/internal/observations")
public class ObservationController {
    private final ObservationStore observationStore;
    public ObservationController(ObservationStore observationStore) { this.observationStore = observationStore; }

    @GetMapping("/traces/{traceId}")
    public ResponseEntity<?> trace(@PathVariable String traceId) {
        List<ObservationRecord> records = observationStore.get(traceId);
        if (records.isEmpty()) {
            return ResponseEntity.status(404).body(java.util.Map.of("error", "TRACE_NOT_FOUND"));
        }
        return ResponseEntity.ok(records);
    }
}
```

- [ ] **Step 7: 集成验证**

启动两个服务,发起一次 check-stock 调用,然后:

```bash
curl -s -D- -o /dev/null -X POST http://localhost:8081/api/orders/1/check-stock -H "Content-Type: application/json" -d '{"skuId":42,"warehouseId":7,"quantity":1}'   # 记下响应头 x-trace-id
curl -s http://localhost:8081/internal/observations/traces/<traceId>
curl -s http://localhost:8082/internal/observations/traces/<traceId>
curl -s http://localhost:8081/internal/observations/traces/nonexistent   # 404 TRACE_NOT_FOUND
```

Expected: order 返回含 `order.total`/`order.inventory_http`;inventory 返回含 `inventory.total`/`inventory.database`;不存在的返回 404。

- [ ] **Step 8: 提交**

```bash
git add java/common java/order-service java/inventory-service
git commit -m "feat(obs): ObservationStore 阶段耗时记录 + traces 观测端点"
```

---

### Task 1.7: Micrometer 指标 + 指标汇总端点

**Files:**
- Create: `java/common/src/main/java/com/tracemind/common/obs/MetricsCollector.java`
- Modify: `java/common/src/main/java/com/tracemind/common/obs/ObservationController.java`(追加 metrics 端点)
- Test: `java/common/src/test/java/com/tracemind/common/obs/MetricsCollectorTest.java`

**Interfaces:**
- Consumes: `ObservationStore.recent(windowSeconds)`(Task 1.6)。
- Produces: `GET /internal/observations/metrics?window_seconds=300` → `{"service":"order-service","window_seconds":300,"p95_ms":..., "qps":..., "error_rate":..., "representative_slow_trace_id":"..."}`。Task 2.x 的 `get_service_metrics` 调用此端点。

- [ ] **Step 1: 写失败测试**

`MetricsCollectorTest.java`:

```java
package com.tracemind.common.obs;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class MetricsCollectorTest {
    @Test
    void computesP95QpsAndSlowTrace() {
        MeterRegistry registry = new SimpleMeterRegistry();
        ObservationStore store = new ObservationStore(10_000, 600_000);
        store.record("s", "t1", "total", 100, true);
        store.record("s", "t2", "total", 200, true);
        store.record("s", "t3", "total", 300, true);
        store.record("s", "t4", "total", 400, true);
        store.record("s", "t5", "total", 500, true);   // 5 条,p95 = 第 5 条(排序后 500)
        MetricsCollector collector = new MetricsCollector(registry, store, "s");
        MetricsCollector.Summary summary = collector.summary(300);
        assertThat(summary.p95Ms()).isEqualTo(500);
        assertThat(summary.qps()).isEqualTo(5.0 / 300.0, within(0.001));
        assertThat(summary.errorRate()).isZero();
        assertThat(summary.representativeSlowTraceId()).isEqualTo("t5");
    }

    @Test
    void emptyWindowReturnsNulls() {
        MetricsCollector collector = new MetricsCollector(new SimpleMeterRegistry(), new ObservationStore(10, 600_000), "s");
        MetricsCollector.Summary summary = collector.summary(300);
        assertThat(summary.p95Ms()).isNull();
        assertThat(summary.representativeSlowTraceId()).isNull();
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd java && mvn -pl common -q test -Dtest=MetricsCollectorTest`
Expected: FAIL — 类不存在。

- [ ] **Step 3: 写 MetricsCollector**

```java
package com.tracemind.common.obs;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.Comparator;
import java.util.List;

@Component
public class MetricsCollector {
    private final MeterRegistry meterRegistry;
    private final ObservationStore store;
    private final String serviceName;
    private final Timer timer;

    public MetricsCollector(MeterRegistry meterRegistry, ObservationStore store,
                            @Value("${spring.application.name}") String serviceName) {
        this.meterRegistry = meterRegistry;
        this.store = store;
        this.serviceName = serviceName;
        this.timer = Timer.builder("http.server.requests.duration")
                .publishPercentiles(0.95)
                .register(meterRegistry);
    }

    /** 供各服务拦截器调用:同时计入 Micrometer Timer 与错误率。 */
    public void record(long durationMs, boolean success) {
        timer.record(Duration.ofMillis(durationMs));
        if (!success) {
            Counter.builder("http.server.requests.errors")
                    .tag("service", serviceName)
                    .register(meterRegistry).increment();
        }
    }

    /** 内部汇总端点:固定结构,不暴露 Actuator 原始响应。 */
    public Summary summary(long windowSeconds) {
        List<ObservationRecord> recs = store.recent(windowSeconds).stream()
                .filter(r -> r.service().equals(serviceName) && r.stage().equals("total"))
                .toList();
        if (recs.isEmpty()) {
            return new Summary(serviceName, windowSeconds, null, 0.0, null, null);
        }
        List<Long> durations = recs.stream().map(ObservationRecord::durationMs)
                .sorted().toList();
        long p95 = durations.get((int) Math.ceil(durations.size() * 0.95) - 1);
        long successes = recs.stream().filter(ObservationRecord::success).count();
        double errorRate = (recs.size() - successes) / (double) recs.size();
        ObservationRecord slowest = recs.stream()
                .max(Comparator.comparingLong(ObservationRecord::durationMs)).orElseThrow();
        return new Summary(serviceName, windowSeconds, p95,
                recs.size() / (double) windowSeconds, errorRate, slowest.traceId());
    }

    public record Summary(String service, long windowSeconds, Long p95Ms,
                          double qps, Double errorRate, String representativeSlowTraceId) {}
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd java && mvn -pl common -q test -Dtest=MetricsCollectorTest`
Expected: PASS。

- [ ] **Step 5: 追加 metrics 端点(ObservationController)**

```java
@GetMapping("/metrics")
public ResponseEntity<?> metrics(@RequestParam(name = "window_seconds", defaultValue = "300") long windowSeconds) {
    MetricsCollector.Summary summary = metricsCollector.summary(windowSeconds);
    return ResponseEntity.ok(summary);
}
```

`ObservationController` 构造器注入 `MetricsCollector`(serviceName 已在其内部,`@Value("${spring.application.name}")`)。

- [ ] **Step 6: 两个服务拦截器接入 MetricsCollector**

修改 `OrderTraceInterceptor` 与 `InventoryTraceInterceptor`:构造器注入 `MetricsCollector`,在 `finally` 块中追加(保持原有 `observationStore.record(...)` 调用不变,两处写入互不替代):

```java
metricsCollector.record(totalMs, response.getStatus() < 500);
```

- [ ] **Step 7: 集成验证**

Run:

```bash
# 先跑一点负载(见 Task 1.9 loadgen,或手动调几次 check-stock)
curl -s "http://localhost:8081/internal/observations/metrics?window_seconds=300"
curl -s "http://localhost:8082/internal/observations/metrics?window_seconds=300"
```

Expected: 返回固定结构,含 p95_ms/qps/error_rate/representative_slow_trace_id;无请求时 p95_ms 为 null。

- [ ] **Step 8: 提交**

```bash
git add java/common
git commit -m "feat(obs): 指标汇总端点(P95/QPS/错误率/最慢 trace)"
```

---

### Task 1.8: 场景控制 SCN-001(注入/重置/状态)

**Files:**
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioService.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioController.java`
- Create: `java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioAuditMapper.java`
- Test: `java/inventory-service/src/test/java/com/tracemind/inventory/scenario/ScenarioServiceTest.java`

**Interfaces:**
- Consumes: `inventory` 表与 `idx_sku_warehouse`、`scenario_audit` 表(Task 1.2);`ObservationStore.clear()`(Task 1.6)。
- Produces: `ScenarioService.inject(): InjectResult`、`reset(): ResetResult`、`status(): ScenarioStatus`;状态码 `FAULTY`(索引缺失)/`HEALTHY`(索引存在)。`DEMO_MODE` 关闭时返回 403。M3 将在此基础上增加"运行中 Incident 禁止 reset"。

- [ ] **Step 1: 写失败测试(Service 层,用 Mockito 模拟 JdbcTemplate)**

`ScenarioServiceTest.java`:

```java
package com.tracemind.inventory.scenario;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ScenarioServiceTest {
    @Mock JdbcTemplate jdbcTemplate;
    @InjectMocks ScenarioService scenarioService;

    @Test
    void injectDropsIndex() {
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(1);
        ScenarioService.InjectResult result = scenarioService.inject();
        assertThat(result.status()).isEqualTo("FAULTY");
        verify(jdbcTemplate).execute("ALTER TABLE inventory DROP INDEX idx_sku_warehouse");
    }

    @Test
    void statusReflectsIndexPresence() {
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(1);
        assertThat(scenarioService.status().indexPresent()).isTrue();
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(0);
        assertThat(scenarioService.status().indexPresent()).isFalse();
    }
}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd java && mvn -pl inventory-service -q test -Dtest=ScenarioServiceTest`
Expected: FAIL — 类不存在。

- [ ] **Step 3: 写 ScenarioService**

```java
package com.tracemind.inventory.scenario;

import com.tracemind.common.obs.ObservationStore;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class ScenarioService {
    private final JdbcTemplate jdbcTemplate;
    private final ObservationStore observationStore;

    public ScenarioService(JdbcTemplate jdbcTemplate, ObservationStore observationStore) {
        this.jdbcTemplate = jdbcTemplate;
        this.observationStore = observationStore;
    }

    public InjectResult inject() {
        Integer present = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() " +
                "AND table_name = 'inventory' AND index_name = 'idx_sku_warehouse'", Integer.class);
        if (present == null || present == 0) {
            return new InjectResult("FAULTY", "already_faulty");   // 幂等
        }
        jdbcTemplate.execute("ALTER TABLE inventory DROP INDEX idx_sku_warehouse");
        observationStore.clear();
        return new InjectResult("FAULTY", "injected");
    }

    public ResetResult reset() {
        Integer present = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() " +
                "AND table_name = 'inventory' AND index_name = 'idx_sku_warehouse'", Integer.class);
        if (present == null || present == 0) {
            jdbcTemplate.execute("ALTER TABLE inventory ADD INDEX idx_sku_warehouse (sku_id, warehouse_id)");
        }
        observationStore.clear();
        return new ResetResult("HEALTHY", "reset");
    }

    public ScenarioStatus status() {
        Integer present = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() " +
                "AND table_name = 'inventory' AND index_name = 'idx_sku_warehouse'", Integer.class);
        return new ScenarioStatus(present != null && present > 0);
    }

    public record InjectResult(String status, String detail) {}
    public record ResetResult(String status, String detail) {}
    public record ScenarioStatus(boolean indexPresent) {}
}
```

`ScenarioAuditMapper.java`(MyBatis-Plus `BaseMapper<ScenarioAudit>` + 实体 `scenario_audit`;审计写入在 Controller 层完成):

```java
package com.tracemind.inventory.scenario;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface ScenarioAuditMapper extends BaseMapper<ScenarioAudit> {
}
```

实体 `ScenarioAudit`(字段 id/scenarioId/action/actor/detail/createdAt,标准 getter/setter)。

- [ ] **Step 4: 写 ScenarioController(带 DEMO_MODE 与密钥校验)**

```java
package com.tracemind.inventory.scenario;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

import java.time.Instant;

@RestController
@RequestMapping("/internal/scenarios/SCN-001")
public class ScenarioController {
    private final ScenarioService scenarioService;
    private final ScenarioAuditMapper auditMapper;
    private final JdbcTemplate jdbcTemplate;
    private final boolean demoMode;
    private final String demoKey;

    public ScenarioController(ScenarioService scenarioService, ScenarioAuditMapper auditMapper,
                              JdbcTemplate jdbcTemplate,
                              @Value("${DEMO_MODE:false}") boolean demoMode,
                              @Value("${DEMO_KEY:}") String demoKey) {
        this.scenarioService = scenarioService;
        this.auditMapper = auditMapper;
        this.jdbcTemplate = jdbcTemplate;
        this.demoMode = demoMode;
        this.demoKey = demoKey;
    }

    @PostMapping("/inject")
    public ResponseEntity<?> inject(@RequestHeader(value = "x-demo-key", required = false) String key) {
        if (!demoMode) return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        if (!demoKey.equals(key)) return ResponseEntity.status(401).body(Map.of("error", "invalid demo key"));
        ScenarioService.InjectResult result = scenarioService.inject();
        audit("inject", key);
        return ResponseEntity.ok(result);
    }

    @PostMapping("/reset")
    public ResponseEntity<?> reset(@RequestHeader(value = "x-demo-key", required = false) String key) {
        if (!demoMode) return ResponseEntity.status(403).body(Map.of("error", "DEMO_MODE disabled"));
        if (!demoKey.equals(key)) return ResponseEntity.status(401).body(Map.of("error", "invalid demo key"));
        ScenarioService.ResetResult result = scenarioService.reset();
        audit("reset", key);
        return ResponseEntity.ok(result);
    }

    @GetMapping("/status")
    public ResponseEntity<ScenarioService.ScenarioStatus> status() {
        if (!demoMode) return ResponseEntity.status(403).body(null);
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
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd java && mvn -pl inventory-service -q test -Dtest=ScenarioServiceTest`
Expected: PASS。

- [ ] **Step 6: 集成验证(真实 MySQL)**

Run:

```bash
curl -s -X POST http://localhost:8082/internal/scenarios/SCN-001/inject -H "x-demo-key: $DEMO_KEY"     # FAULTY
mysql -uapp_business -papp_business_pwd tracemind_business -e "SHOW INDEX FROM inventory;"             # 无 idx_sku_warehouse
curl -s -X POST http://localhost:8082/internal/scenarios/SCN-001/reset -H "x-demo-key: $DEMO_KEY"      # HEALTHY
mysql -uapp_business -papp_business_pwd tracemind_business -e "SHOW INDEX FROM inventory;"             # 有 idx_sku_warehouse
curl -s http://localhost:8082/internal/scenarios/SCN-001/status                                          # {"indexPresent":true}
curl -s -X POST http://localhost:8082/internal/scenarios/SCN-001/inject                                 # 401(缺密钥)
mysql -uapp_business -papp_business_pwd tracemind_business -e "SELECT * FROM scenario_audit ORDER BY id DESC LIMIT 3;"  # 审计记录
```

Expected: 注入/重置幂等生效、密钥校验生效、审计落库。

- [ ] **Step 7: 提交**

```bash
git add java/inventory-service
git commit -m "feat(scenario): SCN-001 注入/重置/状态接口(DEMO_MODE + 密钥 + 审计)"
```

---

### Task 1.9: 负载发生器 + M1 验收

**Files:**
- Create: `scripts/loadgen.py`
- Create: `scripts/run-load.ps1`
- Create: `scripts/verify-m1.ps1`
- Test: 手动执行(见 Step 3)

**Interfaces:**
- Consumes: `POST /api/orders/{orderId}/check-stock`(Task 1.5)、`/internal/observations/metrics`(Task 1.7)。
- Produces: 可配置并发/时长/QPS 的负载脚本;验收脚本输出"有索引 vs 无索引"的 EXPLAIN 与 P95 对比。

- [ ] **Step 1: 写 loadgen.py**

```python
"""负载发生器:循环调用 order check-stock。用法见 run-load.ps1"""
import os
import random
import sys
import time
import urllib.request

ORDER_URL = os.environ.get("ORDER_SERVICE_URL", "http://localhost:8081")
DURATION_SECONDS = int(os.environ.get("LOAD_DURATION_SECONDS", "60"))
QPS = int(os.environ.get("LOAD_QPS", "20"))


def call() -> None:
    sku = random.randint(0, 19999)
    wh = random.randint(0, 49)
    body = f'{{"skuId":{sku},"warehouseId":{wh},"quantity":1}}'.encode()
    req = urllib.request.Request(
        f"{ORDER_URL}/api/orders/1/check-stock",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req).read()


def main() -> int:
    interval = 1.0 / QPS
    deadline = time.time() + DURATION_SECONDS
    sent = 0
    while time.time() < deadline:
        call()
        sent += 1
        time.sleep(interval)
    print(f"loadgen done: {sent} requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 写 run-load.ps1 与 verify-m1.ps1**

`run-load.ps1`:

```powershell
param([int]$Seconds = 60, [int]$Qps = 20)
$env:LOAD_DURATION_SECONDS = "$Seconds"
$env:LOAD_QPS = "$Qps"
python scripts/loadgen.py
```

`verify-m1.ps1`(核心验收:对比索引存在与否的 EXPLAIN 与 P95):

```powershell
param([string]$MysqlPassword = "app_business_pwd")
# 1) 确保 HEALTHY,采集基线
curl.exe -s -X POST "http://localhost:8082/internal/scenarios/SCN-001/reset" -H "x-demo-key: $env:DEMO_KEY" | Out-Null
& python scripts/loadgen.py   # 短负载(LOAD_DURATION_SECONDS=20)
$healthyP95 = (curl.exe -s "http://localhost:8082/internal/observations/metrics?window_seconds=300" | ConvertFrom-Json).p95_ms
# 2) 注入故障,再采集
curl.exe -s -X POST "http://localhost:8082/internal/scenarios/SCN-001/inject" -H "x-demo-key: $env:DEMO_KEY" | Out-Null
& python scripts/loadgen.py
$faultyP95 = (curl.exe -s "http://localhost:8082/internal/observations/metrics?window_seconds=300" | ConvertFrom-Json).p95_ms
# 3) EXPLAIN 对比
$explainHealthy = & mysql -uapp_business -p"$MysqlPassword" tracemind_business -e "EXPLAIN SELECT id FROM inventory WHERE sku_id=42 AND warehouse_id=7;"
$explainFaulty  = & mysql -uapp_business -p"$MysqlPassword" tracemind_business -e "EXPLAIN SELECT id FROM inventory WHERE sku_id=42 AND warehouse_id=7;"  # 需在 faulty 态执行,此处简化:见 Step 3 手动步骤
Write-Host "healthy p95=$healthyP95  faulty p95=$faultyP95"
```

> 说明:verify-m1.ps1 中 EXPLAIN 的"故障态"采集需在 inject 后执行(脚本内顺序已保证)。验收以人工对比输出为准。

- [ ] **Step 3: 执行 M1 验收**

Run(按顺序):

```bash
# 1) 启动两个服务(或确认已启动)
# 2) HEALTHY 态跑 20s 负载,记 P95
powershell -ExecutionPolicy Bypass -File scripts/run-load.ps1 -Seconds 20 -Qps 20
curl -s "http://localhost:8082/internal/observations/metrics?window_seconds=300"
# 3) 注入故障,再跑 20s 负载
curl -s -X POST http://localhost:8082/internal/scenarios/SCN-001/inject -H "x-demo-key: $env:DEMO_KEY"
powershell -ExecutionPolicy Bypass -File scripts/run-load.ps1 -Seconds 20 -Qps 20
curl -s "http://localhost:8082/internal/observations/metrics?window_seconds=300"
# 4) EXPLAIN 对比(故障态)
mysql -uapp_business -papp_business_pwd tracemind_business -e "EXPLAIN FORMAT=JSON SELECT id FROM inventory WHERE sku_id=42 AND warehouse_id=7\G"
# 5) reset 恢复
curl -s -X POST http://localhost:8082/internal/scenarios/SCN-001/reset -H "x-demo-key: $env:DEMO_KEY"
```

Expected: 健康态 EXPLAIN 使用 `idx_sku_warehouse`(`possible_keys`/`key` 非空、`rows` 小);故障态 `type=ALL`、`rows` 接近全表;故障态 P95 明显高于健康态。**此差异即 M1 验收标准。**

- [ ] **Step 4: 提交**

```bash
git add scripts/loadgen.py scripts/run-load.ps1 scripts/verify-m1.ps1
git commit -m "feat(scripts): 负载发生器与 M1 验收脚本(EXPLAIN + P95 对比)"
```

- [ ] **Step 5: 更新 README 开发说明(可选但推荐)**

`README.md` 记录:M1 启动步骤(init-database → generate-data → 两个服务)、`DEMO_MODE/DEMO_KEY` 说明、验收命令。提交:`git add README.md && git commit -m "docs: M1 开发启动与验收说明"`。

---

## 后续里程碑(进入各阶段时用 writing-plans 细化)

- **M2 AI 服务与工具层**(依赖 M1 全部):FastAPI 骨架、控制库 13 表、Incident 基线采集、七受控工具(五个调查工具调 M1 的观测/查询接口)、四账号三连接池、审计与 `incident_event`。验收:不调 LLM 可手动取齐 E1~E5 证据。
- **M3 LangGraph 闭环**(依赖 M2):九节点图、调查预算、E1~E5 根因闸门、恢复规则、AsyncSqliteSaver、审批 interrupt/恢复、过期审批扫描、幂等修复(no_op)、复盘报告。验收:纯 API 完成闭环。
- **M4 Vue 工作台**(依赖 M2/M3 的 API 与 SSE):三个页面、Vite Proxy、SSE 断线补发。验收:不碰命令行可演示。
- **M5 最终工程交付**(依赖 M1~M4):Testcontainers 补全、前后端自动化测试、Dockerfile、Docker Compose、E2E、README/架构图/演示脚本。验收:全新环境 Compose 一次启动并重复演示。
