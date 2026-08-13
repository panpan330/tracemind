# TraceMind GitHub Actions 手工配置指南

本文档说明 CI 流水线(Fast 持续门禁 + Full 手动发布验收)在 GitHub 仓库上的配置步骤。换新仓库可据此复现完整 CI。

## 1. 前置

- 仓库:私有或公开均可;Actions 在私有仓也免费(有配额)。
- **分支保护限制**:GitHub Free 私有仓的 Branch Protection / Rulesets 通常需 Pro;Free 主要向公开仓提供。开发期私有无 Pro → fast-gate 会执行但可能无法强制阻断合并;求职前转公开或升级 Pro 后再启用 Required Check。

## 2. Secrets 配置(仓库 Settings → Secrets and variables → Actions)

Full E2E 需要以下 Secret(全部在 `full-e2e` Environment 或仓库级):

| Secret | 说明 |
|---|---|
| `MYSQL_ROOT_PASSWORD` | CI MySQL root 密码(fixed,非复用其他环境) |
| `TRACEMIND_DB_CONTROL_APP_PASSWORD` | 控制库应用账号密码 |
| `TRACEMIND_DB_APP_BUSINESS_PASSWORD` | 业务库应用账号密码 |
| `TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD` | 只读调查账号密码 |
| `TRACEMIND_DB_FIX_EXECUTOR_PASSWORD` | 处置账号密码 |
| `TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD` | 会话终止账号密码 |
| `TRACEMIND_CHAT_API_KEY` | 百炼 API Key(有额度,勿泄露) |
| `TRACEMIND_CHAT_BASE_URL` | 百炼 Base URL |
| `TRACEMIND_CHAT_MODEL` | 主模型 |
| `TRACEMIND_EVAL_CHAT_MODEL` | 评测固定快照模型(如 qwen3.7-plus-2026-05-26) |

> 建议放 **Environment `full-e2e`**(而非仓库级):`full-e2e.yml` 中只有 full-e2e Job 绑定该 Environment,Secrets 仅在该 Job 可用;preflight / verify-fast-gate 不接触它们。Fast 各 Job 不注入任何真实凭据。

## 3. Environment 配置

- Settings → Environments → New environment → 命名 `full-e2e`。
- 加 Secrets(上表)。
- **Deployment branch/tag 限制**:只允许 `main`(`release_ref` 只表示"待测代码 Ref",Workflow 本身永远从 main 执行)。

## 4. Workflow 最小权限

两个 workflow 文件已声明最小权限:

- `fast-gate.yml`:`permissions: { contents: read }`(汇聚 Job 需 download-artifact,由 actions 自带 token 处理)。
- `full-e2e.yml`:`permissions: { contents: read }`;`verify-fast-gate` Job 额外 `checks: read, actions: read`(读 Check Runs)。

## 5. Fast Gate Required Check(公开仓 / Pro 私有仓)

- Settings → Branches → Branch protection rule(或 Rulesets)→ 勾选 `Require status checks to pass` → 选择 **`fast-gate`**(汇聚 Job)。
- 该 Job 名与 workflow 名一致(`name: fast-gate`)。

## 6. 手动触发 Full E2E

1. 推送代码到 `main`(Fast 自动跑)。
2. Actions → 选 `full-e2e` workflow → Run workflow。
3. 输入:
   - `scope`: `smoke`(最小真实验收,推荐先跑)或 `release`(全量)。
   - `confirm`: 必须输入 `RUN_FULL_E2E`。
   - `release_ref`: 留空测当前 main;或填 `v1.6.0` 风格 SemVer tag(必须是 main 祖先)。
4. 观察 Job 链:preflight → verify-fast-gate → full-e2e。

## 7. Artifact 保留

- Artifact 默认保留 90 天;如需调整,在 workflow 的 `upload-artifact` step 加 `retention-days`。

## 8. 轮换百炼 Key

1. 百炼控制台生成新 Key。
2. 更新 `full-e2e` Environment 的 `TRACEMIND_CHAT_API_KEY`。
3. 旧 Key 失效。
4. 确认 `.env.local` / VM `.env.vm` 同步(`TRACEMIND_EVAL_CHAT_MODEL` 评测固定快照勿动)。

## 9. 确认日志未泄密

- Full E2E 日志经 `scripts/ci/redact_and_upload.sh` 脱敏;Artifact 只含 `reports/generated/sanitized/`。
- 推送前跑 `python scripts/ci/scan_secrets.py`(工作区)与 `git log -p | grep -iE 'sk-|panhangyu|192.168'`(历史)。
- 若历史含真实凭据:用 `git filter-repo` 清理或轮换 key。

## 10. 触发边界

- `full-e2e.yml`:`workflow_dispatch` + `github.ref == refs/heads/main`(preflight 首条断言,非 main 直接失败且未注入 Secret)。
- `concurrency: group: full-e2e, cancel-in-progress: false`(绝不取消进行中的处置)。
