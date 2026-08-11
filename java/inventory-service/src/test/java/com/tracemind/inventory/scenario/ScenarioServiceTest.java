package com.tracemind.inventory.scenario;

import com.tracemind.common.obs.ObservationStore;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ScenarioServiceTest {
    @Mock
    JdbcTemplate jdbcTemplate;
    @Mock
    ObservationStore observationStore;
    @InjectMocks
    ScenarioService scenarioService;

    @Test
    void injectDropsIndex() {
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(1);
        ScenarioService.InjectResult result = scenarioService.inject("SCN-001");
        assertThat(result.status()).isEqualTo("FAULTY");
        verify(jdbcTemplate).execute("DROP INDEX idx_sku_warehouse ON inventory");
    }

    @Test
    void injectIsIdempotent_whenAlreadyFaulty() {
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(0);
        ScenarioService.InjectResult result = scenarioService.inject("SCN-001");
        assertThat(result.status()).isEqualTo("FAULTY");
        assertThat(result.detail()).isEqualTo("already_faulty");
    }

    @Test
    void resetRecreatesIndex_whenMissing() {
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(0);
        ScenarioService.ResetResult result = scenarioService.reset("SCN-001");
        assertThat(result.status()).isEqualTo("HEALTHY");
        verify(jdbcTemplate).execute("CREATE INDEX idx_sku_warehouse ON inventory (sku_id, warehouse_id)");
    }

    @Test
    void statusReflectsIndexPresence() {
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(1);
        assertThat(scenarioService.status().indexPresent()).isTrue();
        when(jdbcTemplate.queryForObject(anyString(), eq(Integer.class))).thenReturn(0);
        assertThat(scenarioService.status().indexPresent()).isFalse();
    }
}
