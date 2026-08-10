package com.tracemind.inventory.scenario;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

@TableName("scenario_audit")
public class ScenarioAudit {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String scenarioId;
    private String action;
    private String actor;
    private String detail;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getScenarioId() { return scenarioId; }
    public void setScenarioId(String scenarioId) { this.scenarioId = scenarioId; }
    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }
    public String getActor() { return actor; }
    public void setActor(String actor) { this.actor = actor; }
    public String getDetail() { return detail; }
    public void setDetail(String detail) { this.detail = detail; }
}
