-- V2.1-A:Alertmanager 接入层 —— AlertEvent 原始事件(不可变投递日志)
--          与 AlertInstance 投影(FIRING → RESOLVED 单向,version CAS)。
-- 追加式变化;Incident 关联/聚合属 V2.1-B(后续 migration)。
USE tracemind_control;

-- 不可变投递日志:同一 (source, delivery_hash) 唯一 —— 重放幂等的落库基础
CREATE TABLE IF NOT EXISTS alert_event (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(32) NOT NULL DEFAULT 'alertmanager',
    external_fingerprint VARCHAR(64) NOT NULL,
    alert_instance_key VARCHAR(190) NOT NULL,
    delivery_hash CHAR(64) NOT NULL,
    alert_status VARCHAR(16) NOT NULL,
    starts_at DATETIME(3) NOT NULL,
    ends_at DATETIME(3) NULL,
    received_at DATETIME(3) NOT NULL,
    labels_json JSON NOT NULL,
    annotations_json JSON NULL,
    payload_json JSON NULL,
    UNIQUE KEY uk_alert_delivery (source, delivery_hash),
    KEY idx_alert_event_instance (alert_instance_key),
    KEY idx_alert_event_received (received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 当前告警状态投影:只允许 FIRING → RESOLVED 单向推进(version 递增 CAS);
-- 迟到旧 FIRING 只归档到 alert_event,不回退本投影
CREATE TABLE IF NOT EXISTS alert_instance (
    alert_instance_key VARCHAR(190) PRIMARY KEY,
    source VARCHAR(32) NOT NULL DEFAULT 'alertmanager',
    external_fingerprint VARCHAR(64) NOT NULL,
    starts_at DATETIME(3) NOT NULL,
    current_status VARCHAR(16) NOT NULL,
    resolved_at DATETIME(3) NULL,
    last_received_at DATETIME(3) NOT NULL,
    last_event_id BIGINT NULL,
    version INT NOT NULL DEFAULT 1,
    KEY idx_alert_instance_fp (source, external_fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
