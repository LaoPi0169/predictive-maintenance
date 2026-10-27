# M4 实施计划（plan.md）

任务按依赖排序，每组可独立测试。实现顺序 = 测试顺序 = 验收顺序。

## T1. 配置与契约扩展

- `app/schemas.py`：`ToolCallTrace` / `RelevanceVerdict` / `DiagnosisDraft` /
  `KbSearchToolResult` / `WorkOrderDraftInput` / `WorkOrderRecord` /
  `ApprovalDecision` / `AgentRunResult`（全部闭式）。
- `app/config.py` M4 块：`AGENT_TOOL_VERSION="agent-tools-v1"`、
  `AGENT_ACTIONABLE_RISK_LEVELS`、`APPROVER_ROLES`（EXAMPLE_RULE）、
  `LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL /
  LLM_TIMEOUT_S`（环境变量，密钥不进源码）、`WORKORDER_ID_PREFIX="WO-"`。
- 测试：`tests/test_schemas.py` 追加（未知字段拒绝、`approved` 之类
  伪造字段被拒、枚举边界）。

## T2. LLM 端口与三 provider

- `app/agent/llm.py`：`ChatModelPort.complete(system, user) -> str`；
  `build_chat_model(config)` 按 `LLM_PROVIDER` 装配：
  - `none` → 显式降级对象；
  - `scripted` → 确定性规则实现（相关性：泛化词表 + 域词重叠；
    诊断：证据→故障模式映射 + SOP 步骤抽取）；
  - `openai_compatible` → stdlib urllib POST `/chat/completions`，
    超时/非 200/JSON 解析失败 → `LlmUnavailableError / LlmResponseInvalidError`。
- `check_relevance(query, hits)`：只降级不升级 `can_answer`；
  `synthesize_diagnosis(assessment, hits)`：输出 `DiagnosisDraft`，
  JSON 非法 → 显式失败态。
- 测试：`tests/test_agent_llm.py`（scripted 确定性、泛化词域外拒绝、
  真实域内放行、none 降级留痕、openai_compatible 失败语义用本地
  socket/monkeypatch 验证，不联网）。

## T3. 工单服务与仓储

- `app/database/repository.py` 追加 `work_orders` / `agent_runs` 读写。
- `app/workorders/service.py`：
  - `create_draft(...)`：幂等键 `sha256(asset|window|type)` 派生；同键
    返回既有记录（`duplicate=true`）；创建即校验（无证据/无动作 →
    `WorkOrderValidationError` 不建单）；DRAFT → PENDING_APPROVAL 同事务；
  - `approve(...)`：服务端校验角色白名单 + 当前状态；APPROVE/REJECT；
  - `submit(...)`：仅 `status==APPROVED && approved_by` 时执行，写
    `SUBMITTED + external_ref`；重复提交幂等；REJECTED 终态 409。
- 测试：`tests/test_workorder_service.py`（幂等、状态机非法迁移、
  角色白名单、审批记录服务端校验）。

## T4. 工具层（spec §6.3 六工具）

- `app/agent/tools.py`：`ToolSpec`（名称/版本/effect_category/超时/错误类）
  + 注册表；闭式输入输出；统一执行包装（计时、异常归类、轨迹记录）。
  - `get_asset_status`（read_only）：主数据 + 最新读数 + 质量态；
  - `get_prediction`（read_only，审计留痕副作用）：调 M2 评估服务；
  - `search_maintenance_kb`（read_only）：库内调用 M3 检索器 + 相关性复核；
  - `check_spare_parts`（read_only）：`inventory.compatible_models`
    主数据核对 → CONFIRMED/PENDING/UNAVAILABLE；
  - `create_work_order_draft`（write_preview）：包装 T3 服务；
  - `submit_work_order`（write_execute）：包装 T3 服务，伪造
    `approved` 字段被闭式 schema 拒绝。
- 测试：`tests/test_agent_tools.py`（每工具的成功/空/非法参数/未知字段/
  依赖缺失/越权语义 + 轨迹完整性）。

## T5. LangGraph 图与运行器

- `app/agent/graph.py`：`StateGraph` 节点
  `validate_asset → assess_risk → search_kb → diagnose → check_parts →
  draft_work_order → finalize`，条件边：
  未知资产/无法评估 → fail 节点；风险不可操作 → 跳过 KB 与工单；
  KB 不可答 → 跳过诊断综合；重复幂等键 → 复用工单。
- `app/agent/runner.py`：装配依赖（conn、index、llm）、执行图、
  `agent_runs` 落盘（状态/轨迹/结果）。
- 测试：`tests/test_agent_graph.py`（高风险出单、健康不出单、404、
  503 降级、LLM 未配置降级、agent_runs 留痕、注入语料零副作用）。

## T6. API

- `app/main.py`：`POST /api/v1/maintenance/assess`、
  `GET/POST /api/v1/work-orders`、`POST /api/v1/work-orders/{id}/approve`、
  `POST /api/v1/work-orders/{id}/submit`（`X-Approver-Id/Role` 头校验）。
- 错误态：404 / 422 / 403 / 409 / 503（结构化错误体）。
- 测试：`tests/test_agent_api.py` + `tests/test_workorder_api.py`
  （全链路 assess→approve→submit、审批门禁、幂等、错误态矩阵）。

## T7. Agent 评测

- `data/knowledge/agent_eval_scenarios.json`：确定性场景集（正常/健康/
  未知/数据不足/相关性拒绝/幂等/注入/LLM 降级/备件三态/未授权）。
- `scripts/evaluate_agent.py`：跑场景矩阵 → 场景通过率、引用有效率、
  幂等重复数、未授权提交数 → `data/processed/agent_evaluation.json`。
- 测试：`tests/test_agent_evaluation.py`（门禁：场景通过率=1.0、
  幂等重复=0、未授权提交=0、引用有效率=1.0）。

## T8. 收尾

- 全量 `python -m pytest`，记录真实数字；
- 运行 `scripts/evaluate_agent.py`，实测指标写入 validation.md；
- README（M4 ✅、assess/工单 API 示例、目录树）、CHANGELOG、
  requirements.txt（langgraph==1.2.18 依赖注记）；
- 已知局限如实记录（scripted 非 LLM、演示级身份头、EXAMPLE_RULE 阈值）；
- git commit。

## 交付文件清单

```
app/agent/{__init__,llm,tools,graph,runner}.py
app/workorders/{__init__,service}.py
app/{schemas,config,main}.py（扩展）
app/database/repository.py（扩展）
data/knowledge/agent_eval_scenarios.json
scripts/evaluate_agent.py
tests/{test_agent_llm,test_agent_tools,test_agent_graph,test_agent_api,test_workorder_api,test_agent_evaluation}.py
tests/test_schemas.py（扩展）
specs/2026-10-26-milestone-4-agent-tools/
```
