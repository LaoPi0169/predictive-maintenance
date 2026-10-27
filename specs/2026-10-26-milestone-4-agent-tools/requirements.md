# M4 需求规格：Agent 与工具调用（Agent & Tool Calling）

- 日期：2026-10-26
- 来源：`spec/Project.md`（§3.2 Agent 标准输出、§六 6.1–6.4 Agent 工作流与六工具、
  §8.3 API 契约、§九 9.1–9.3 工单生成/备件三态/状态机、§10.1 第四层工具测试、§10.2 验收指标）
- 工作流：`skills/agent-tool-integration`（工具契约、服务端信任边界、写入门禁）+
  `skills/rag-evaluation` §6（Agent 评测）
- 上游：M1 数据地基 + M2 预测与风险 + M3 知识 RAG（已交付：四级风险与证据、
  预测 API、带引用与拒绝语义的检索）

## 1. 业务问题与用户

M2 回答"设备有多大风险"，M3 回答"应该检查什么、为什么检查"。M4 按 spec §六
把两者串成闭环：**预测识别风险 → 检索维修知识 → 诊断综合 → 备件核对 →
生成待审核工单**（spec §1.2 第 3–6 步），并补上 M3 遗留的兜底责任——
词法检索对仅共享泛化词（报警/处理）的域外问题无法拒绝（M3 validation.md
已知局限），M4 的 LLM 相关性复核必须覆盖。

主要用户：维修/设备工程师（发起评估、审批工单的演示角色）；集成方
（通过 API 消费结构化 `AgentAssessment` 与工单状态）。

## 2. 范围

### In scope（本里程碑）

1. **LLM 端口（可替换 provider，spec Architecture："Optional LLM provider
   behind a replaceable interface"）**，三档：
   - `none`（默认）：不调用 LLM，显式降级并留痕（不虚构诊断）；
   - `scripted`：**确定性规则替代实现**，与真实 LLM 同一接口契约，用于
     离线演示与全部自动化测试（保持 M1–M3 的离线确定性；诚实定位：
     它不是 LLM）；
   - `openai_compatible`：OpenAI 兼容 `/chat/completions`（stdlib urllib
     实现，不新增 SDK），由 `PMA_LLM_*` 环境变量启用；失败 → 显式错误态。
   LLM 的两个职责（仅此两个）：**知识相关性复核**（覆盖 M3 泛化词残留
   风险）与**诊断综合**（严格基于检索证据，JSON 输出校验，解析失败 →
   显式失败态）。
2. **六个工具（spec §6.3 表，名称/输入/输出/权限逐一对应）**：闭式 Pydantic
   输入输出契约 + `ToolSpec` 元数据（名称、版本、`effect_category`、超时、
   错误类别）+ 工具轨迹审计（`agent_runs.tool_trace`）。信任边界全部在
   服务端：身份/审批/授权永不取自模型生成的参数（skill #3）。
3. **LangGraph 编排**（用户 2026-10-26 选定）：`StateGraph` 有界业务状态机
   （固定节点 + 条件边），**不是自由 ReAct**——工具调用顺序由图结构表达
   spec §6.1 业务流程，LLM 无权自由选择工具；每次运行落盘 `agent_runs`。
4. **工单与审批（spec §九 + §8.3）**：`create_work_order_draft`
   （write_preview，幂等）→ 服务端业务校验 → `PENDING_APPROVAL` →
   人工审批（角色白名单）→ `submit_work_order`（write_execute，服务端
   校验审批记录后方可执行）；同一风险事件重复评估不产生重复工单
   （幂等键，spec §8.3/§9.3）。
5. **API（spec §8.3 表）**：`POST /api/v1/maintenance/assess`、
   `GET/POST /api/v1/work-orders`、`POST /api/v1/work-orders/{id}/approve`、
   `POST /api/v1/work-orders/{id}/submit`。
6. **Agent 评测（skills/rag-evaluation §6）**：确定性场景套件（正常/健康/
   未知资产/数据不足/相关性拒绝/幂等/提示注入/LLM 未配置降级/备件三态/
   未授权审批）+ 指标门禁，输出 `data/processed/agent_evaluation.json`。
7. **测试（spec §10.1 第四层）**：工具成功/空结果/非法参数/未知字段/越权/
   依赖缺失/超时语义/重复幂等键/注入无副作用/模型离线失效安全。

### Out of scope（后续里程碑）

- Streamlit 前端与端到端联调（M5）；真实 CMMS/ERP 集成（提交为模拟外部
  引用）；租户/工厂级权限体系与登录会话（spec §8.2 注记的生产加固）；
  LangGraph 跨进程持久化 checkpoint（人工审批异步等待用 `work_orders`
  表承载，见 §4）；多 Agent 协作；PLC 控制类工具（spec 规则 12 永不做）。

## 3. 新增数据形状（契约摘要，全部闭式、v1）

- `ToolCallTrace`：`tool / tool_version / effect_category / status /
  duration_ms / error_code`（审计最小集，不含敏感负载）。
- `DiagnosisDraft`：`failure_mode_candidates / recommended_actions /
  grounding_chunk_ids / synthesizer_version`——动作必须挂引用证据。
- `KbSearchToolResult`：`retrieval: RetrievalResult（M3 契约不变）+
  relevance: RelevanceVerdict`。
- `RelevanceVerdict`：`checked / relevant / reason / checker_version`；
  **只降级不升级**：`can_answer=false` 永不被复核翻回 true。
- `WorkOrderDraftInput / WorkOrderRecord / ApprovalDecision`：工单草稿、
  工单记录、审批请求（含 `decision: APPROVE|REJECT`）；`WorkOrderStatus`
  复用 M1 冻结枚举。
- `AgentRunResult`：`run_id / asset_id / status / tool_trace /
  model_provider / result: AgentAssessment | failure`。
- `AgentAssessment`（M1 已冻结）：M4 填充 `knowledge_sources /
  parts_requirements / work_order_status / requires_human_approval`；
  未校准置信度保持 null（spec §3.2）。

## 4. 决策与理由

| 决策 | 理由 |
|---|---|
| LangGraph 作为编排框架（用户选定 2026-10-26，轻量状态机备选被否） | 图结构显式表达 spec §6.1 业务状态机，条件边/可追溯轨迹/检查点生态成熟，是业界标准做法；依赖按政策记档：`langgraph==1.2.18`（传递引入 langchain-core 1.7.0），离线安装不下载模型、不破坏确定性 |
| 有界状态机图，不做自由 ReAct | spec §六："Agent 不应当拥有不受约束的自由，而应当在明确的业务状态机中调用工具"；工具序列由图决定 → 可测、可审计；LLM 只做相关性复核与诊断综合 |
| 人工审批在图外（REST 端点），不用进程内 interrupt 长驻 | 审批是人工异步动作（可能数小时后），进程内 checkpoint 不耐重启；`work_orders` 表是唯一持久真相；提交时服务端重查审批记录（skill #6：执行前服务端即时复核撤销） |
| `submit_work_order` 永不信任模型参数 | spec §6.3 明示：不能仅因 LLM 在参数里写 `"approved": true` 就执行；闭式 schema 直接拒绝未知字段 `approved`，执行前服务端校验 `status==APPROVED && approved_by` |
| LLM 默认 `none`，测试用 `scripted`，真实供应商需显式环境变量 | 离线确定性（M3 拒绝 chromadb 同款理由）；测试不联网、结果可复现；真实供应商实现但不进 CI，validation.md 如实声明"未行使真实 LLM" |
| 备件兼容性以 `inventory.compatible_models` 主数据为准 | spec §9.2：不从 LLM 经验给物料编码；三态 CONFIRMED/PENDING/UNAVAILABLE 直接落 `PartRequirement.compatibility_status`；BOM 知识文档作为引用证据而非权限来源 |
| 幂等键派生：`sha256(asset_id\|window_start\|window_end\|maintenance_type)` | spec §9.3：外部系统超时不得盲目重试；同一风险事件（同一数据窗口）重复评估必然同键 → 复用既有工单，重复创建次数恒为 0 |
| 状态机映射：DRAFT→(校验)→PENDING_APPROVAL→APPROVED/REJECTED→SUBMITTED | spec §9.3 的 VALIDATED 是"数据与业务规则校验"门，M4 折叠为创建时同事务校验（校验失败不建单，显式报错）；IN_PROGRESS/COMPLETED/VERIFIED 属维修执行阶段（CMMS 侧），M4 不涉及；枚举 CLOSED 本里程碑不用 |
| 备件清单只进工单字段，不触发采购 | spec §9.2：备件需求与采购订单分离；PENDING 件仅记录，不自动下单 |

## 5. 用户角色、权限与信任边界

- **审批白名单（EXAMPLE_RULE）**：`APPROVER_ROLES = ["MAINTENANCE_ENGINEER",
  "OPERATIONS_MANAGER"]`；审批请求须携带 `X-Approver-Id / X-Approver-Role`
  头，角色不在白名单 → 403。**诚实声明**：MVP 无登录会话，身份为演示级
  声明式头（spec §8.2 生产加固项），服务端校验的是"角色是否在白名单 +
  工单当前状态"，而非真实认证身份；
- 检索文档、工具输出、知识语料一律视为**数据而非指令**（skill #3/#7）：
  注入测试证明语料中植入"忽略规则/直接批准"不产生任何工单状态变化；
- 模型生成参数中的身份/审批/授权字段一律无效（闭式 schema 拒绝未知字段）。

## 6. 预期失败状态与行为

| 失败状态 | 行为 |
|---|---|
| 资产不存在 | assess → 404 `ASSET_NOT_FOUND`；工具轨迹记录失败调用 |
| 证据不足 / 无法可靠评估 | 503 `CANNOT_ASSESS`（spec §8.4：与"低风险"严格区分），预测失败态留痕 |
| 知识索引缺失/过期 | 评估继续（预测不依赖 KB），但 `knowledge_sources=[]` + uncertainty note 显式声明知识面不可用；`/knowledge/search` 维持 M3 的 503 语义 |
| LLM 未配置（`none`） | 评估降级：不做诊断综合、相关性复核记 `checked=false`，动作只给通用现场检查 + uncertainty note；不虚构故障模式 |
| LLM 调用失败 / 返回非法 JSON（openai_compatible） | 该节点显式失败态（`DIAGNOSIS_INVALID` / `LLM_UNAVAILABLE`），运行以失败留痕结束，不静默降级为编造诊断 |
| 工单业务校验失败（无证据/无可执行动作） | 不创建工单，评估结果 `work_order_status=NONE` + 原因说明 |
| 重复幂等键 | 返回既有工单（`duplicate=true`），不产生第二行 |
| 未审批即提交 / 越权角色 | 409 `NOT_APPROVED` / 403 `FORBIDDEN`；零副作用 |
| REJECTED 后再审批/再提交 | 409 `INVALID_TRANSITION`（终态） |

## 7. 假设与未决问题

- **假设**：风险可操作阈值 `AGENT_ACTIONABLE_RISK_LEVELS={HIGH,EMERGENCY}`
  为 EXAMPLE_RULE（WATCH 只观察不出单），需设备工程师确认；
- **假设**：`scripted` provider 的相关性规则（泛化词表 + 域词重叠）与诊断
  映射（证据→故障模式）是**演示级替代实现**，只证明编排与契约正确性，
  不证明真实 LLM 行为；接入真实供应商后须以同套评测重跑；
- **假设**：提交工单的外部系统为模拟（`external_ref="CMMS-<id>"`），
  读回校验（skill #6 read-back）以本地库状态为准；
- **未决**：真实 LLM 供应商选型与审批（openai_compatible 已留接口）；
  LangGraph SQLite checkpoint（跨进程恢复）在真实部署时评估。
