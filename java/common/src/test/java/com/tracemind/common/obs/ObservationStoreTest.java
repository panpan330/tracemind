package com.tracemind.common.obs;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class ObservationStoreTest {
    @Test
    void recordsAndRetrievesByTraceId() {
        ObservationStore store = new ObservationStore(10_000, 600_000);
        store.record("order-service", "t1", "total", 120, true);
        store.record("order-service", "t1", "inventory_http", 100, true);
        List<ObservationRecord> recs = store.get("t1");
        assertThat(recs).hasSize(2);
        assertThat(recs.get(0).traceId()).isEqualTo("t1");
    }

    @Test
    void evictsExpiredRecords() {
        ObservationStore store = new ObservationStore(10_000, 600_000);
        store.record("order-service", "old", "total", 1, true);
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
