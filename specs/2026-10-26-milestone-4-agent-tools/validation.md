# M4 验证计划（validation.md）

## 1. 测试与验证项

### 单元 / 集成测试（spec §10.1 第四层）
1. 契约：M4 新契约闭式（未知字段拒绝、伪造 `approved` 字段被拒、枚举边界）；
2. LLM 端口：scripted 确定性输出、泛化词域外拒绝（覆盖 M3 残留风险）、
   域内放行、`none` 显式降级、openai_compatible 失败语义（不联网）；
3. 工单服务：幂等键重复创建返回原单、非法状态迁移拒绝、角色白名单、
   服务端审批校验（提交前重查 `APPROVED + approved_by`）；
4. 工具层：六工具契约（成功/空/非法参数/未知字段/依赖缺失）+ 轨迹完整；
5. LangGraph 图：高风险出单、健康不出单、404、503 无法评估、LLM 未配置
   降级、`agent_runs` 留痕、注入语料零副作用；
6. API：assess 与工单四端点全链路 + 错误态矩阵（404/422/403/409/503）。

### Agent 专项评测（skills/rag-evaluation §6）
- 场景套件（确定性）：正常出单、健康不出单、未知资产、数据不足、
  相关性拒绝、幂等重复、提示注入、LLM 降级、备件三态、未授权审批；
- 指标门禁：场景通过率 = 1.0；幂等重复工单数 = 0；未授权提交数 = 0；
  引用有效率（结构校验）= 1.0。

### 回归
- M1–M3 全量测试保持通过（134 项基线）。

## 2. 命令

```bash
python -m pytest
python scripts/evaluate_agent.py
```

## 3. Definition of Done

- [ ] 上述全部测试通过且数字为真实运行结果；
- [ ] 评测场景通过率=1.0，逐场景明细落盘 `data/processed/agent_evaluation.json`；
- [ ] 重复幂等键不产生重复工单，有测试证明；
- [ ] 未审批不可提交、越权角色 403、模型伪造审批字段无效，有测试证明；
- [ ] LLM 未配置时显式降级（不虚构诊断），有测试证明；
- [ ] README / CHANGELOG 更新；scripted provider 非真实 LLM、演示级身份头、
      EXAMPLE_RULE 阈值等局限如实声明。

## 4. 已知局限（如实声明）

- `scripted` provider 是确定性规则替代实现，不是 LLM：评测证明的是编排、
  契约与安全门禁的正确性，不证明真实 LLM 行为；
- 身份为演示级声明式请求头，无真实认证（spec §8.2 生产加固）；
- 提交工单的外部 CMMS 为模拟引用，无真实读回校验；
- 所有阈值（可操作风险级别、审批角色白名单）为 EXAMPLE_RULE。

---

## 实际验证结果（2026-10-27 实测）

### 测试汇总
- `.venv\Scripts\python -m pytest` → **212 passed**：M1–M3 回归 134 项全部保持
  通过，M4 新增 78 项（契约 +10、LLM 端口 15、工单服务 13、工具层 14、
  LangGraph 图 9、Agent API 5、工单 API 8、评测回归 4）。

### Agent 场景评测（`python scripts/evaluate_agent.py` → `data/processed/agent_evaluation.json`）

| 指标 | 实测 | 门禁 |
|---|---|---|
| 场景通过率 | **1.000**（11/11：S1 高风险出单、S2 健康不出单、S3 未知资产、S4 数据不足、S5 泛化词拒绝、S6 幂等、S7 语料注入、S8 LLM 降级、S9 备件三态、S10 越权审批、S11 未审批提交） | = 1.0 ✅ |
| 重复工单数 | **0**（同一风险事件两次评估仅一张工单，第二次幂等复用并留痕） | = 0 ✅ |
| 未授权提交数 | **0**（越权角色 403 / 未审批 409，零副作用） | = 0 ✅ |
| 引用有效率（结构校验） | **1.000**（S1 的 knowledge_sources 全部解析到现行已批准 chunk） | = 1.0 ✅ |

- LLM provider：`scripted`（确定性规则替代实现，**非真实 LLM**）；真实
  openai_compatible 供应商已实现但未在评测中行使（离线确定性原则）。

### 全链路冒烟（真实 API 语义，TestClient + 独立临时库）
- 高风险资产（degradation seed42）→ `POST /maintenance/assess` 200：EMERGENCY →
  `create_work_order_draft` → 工单 `PENDING_APPROVAL`（EMERGENCY 优先级 HIGH）；
  诊断候选 BEARING_DEGRADATION / BEARING_OVERHEAT（confidence 全 null）；
  动作清单抽取自 KB-SOP-001「检查步骤」块并挂引用；备件 6208-2RS CONFIRMED、
  零库存件 UNAVAILABLE 并留痕、不兼容件 PENDING；
- 审批 → 提交全链路：`approve`（角色白名单头）→ APPROVED（approved_by 落库）→
  `submit` → SUBMITTED + `external_ref=CMMS-WO-000001`，重复提交幂等；
- 未知资产 404（agent_runs 仍留痕）、数据不足 503 CANNOT_ASSESS（预测服务同时
  留痕拒绝行）、LLM 未配置显式降级（不虚构故障模式）、知识索引缺失显式降级。

### 开发期发现并修复的缺陷
1. **scripted 诊断规则读错证据**：初版按 M2 不存在的 `ABOVE_CONFIGURED_LIMIT`
   状态过滤，导致永远无故障模式候选 → 改为解析真实证据文本（`z=` 分数 ≥
   WATCH_SCORE、`over limit`、温度斜率 ≥ 阈值），健康窗口零编造候选；
2. **工单行解析引用不存在的 `_parse`**（M2 的同名函数嵌套在
   `list_predictions` 内）→ 新增模块级 `_parse_timestamp`；
3. **finalize 的无工单分支 NameError**（缺 `WorkOrderStatus` 导入）→ 健康资产
   测试捕获后修复；
4. **agent_runs 外键阻止未知资产留痕** → 去掉 FK（失败运行恰是最需审计的），
   表定义注释说明理由。

### 已知局限（在 §4 基础上补充）
- **scripted provider 不是 LLM**：相关性复核是"泛化词表 + 域词重叠"规则、诊断
  综合是"证据状态解析 + SOP 步骤抽取"规则——评测证明编排/契约/安全门禁正确性，
  不证明真实 LLM 行为；接入真实供应商后必须以同套场景重跑（S5/S7/S8 尤其）；
- 审批身份为演示级声明式请求头（`X-Approver-Id/Role` + 服务端白名单），无真实
  认证会话（spec §8.2 生产加固项）；
- 提交工单的 CMMS 为模拟引用（`CMMS-<id>`），读回校验以本地库状态为准；
- `AGENT_ACTIONABLE_RISK_LEVELS={HIGH,EMERGENCY}`、`APPROVER_ROLES`、诊断映射
  表均为 EXAMPLE_RULE，需设备工程师确认；
- Agent 的知识查询由证据指标词 + "检查步骤"确定性构造；换真实语料后检索命中
  分布会变，动作抽取需重新校准。

### DoD 核对
- [x] 全部测试通过（212 passed，数字为真实运行结果）
- [x] 评测场景通过率 = 1.0，逐场景明细落盘 `data/processed/agent_evaluation.json`
- [x] 重复幂等键不产生重复工单（test_workorder_service / S6 场景证明）
- [x] 未审批不可提交、越权角色 403、模型伪造 `approved` 字段无效（测试与 S10/S11 证明）
- [x] LLM 未配置显式降级、不虚构诊断（test_agent_graph / S8 场景证明）
- [x] README / CHANGELOG / requirements.txt 更新；scripted 非真实 LLM、演示级
      身份头、EXAMPLE_RULE 阈值等局限如实声明

---

## 真实 LLM 接入与重跑（2026-10-27 增补）

### 工程改造（provider 无关，先行交付）
- **传输层加固**（`app/agent/llm.py`）：网络错误/超时与 HTTP 429、5xx 按
  `PMA_LLM_MAX_RETRIES`（默认 2）有界重试 + 线性退避；`response_format
  json_object` 默认请求（`PMA_LLM_JSON_MODE`），服务器以 400/422 拒绝时自动
  回退一次普通请求并记录 `json_mode_effective=False`；401/403 等立即显式失败；
  错误信息永不包含 API 密钥；新增 `probe()` 健康检查；
- **探针** `scripts/llm_probe.py`：脱敏配置摘要（只显示密钥长度）→ 最小补全
  探测 → 相关性 JSON 契约往返；退出码 0=就绪 / 1=端点或契约失败 / 2=配置不全；
- **评测参数化** `scripts/evaluate_agent.py --provider scripted|none|openai_compatible`：
  **同一套 11 场景 + 相同门禁**可对任意 provider 重跑；真实 LLM 报告标记
  `deterministic: false` 并记录模型与传输配置；逐场景耗时留档。

### 离线验证
- 新增 10 项 mock HTTP 服务器传输测试（重试后成功、重试耗尽、鉴权错误立即失败、
  JSON 模式 400 回退、密钥不外泄、畸形响应、配置装配）→ 全量 **222 passed**；
- **scripted 基线重跑（重构无回归）**：11/11 场景 PASS，门禁 = 通过率 1.0、
  重复工单 0、越权提交 0、引用有效率 1.0（`data/processed/agent_evaluation.json`）。

### 真实 DeepSeek（deepseek-chat）重跑（2026-10-27 实测）
- 探针（`scripts/llm_probe.py`）：PASS——连通 OK（3311.5ms），相关性契约为真实
  LLM 中文判断（"候选内容为轴承振动检查SOP……领域相关"，checked=True）；
  探针阶段 `json_mode_effective=False`（探针提示词不含 JSON 字样触发
  DeepSeek 400 → 自动回退普通请求成功），评测阶段提示词含 JSON 未触发 400
  （`json_mode_effective=True`）——**回退机制按请求正确工作**；
- 同套 11 场景重跑（`--provider openai_compatible` →
  `data/processed/agent_evaluation_deepseek.json`，全程 ~49s）：

| 指标 | scripted 基线 | DeepSeek 实测 | 门禁 |
|---|---|---|---|
| 场景通过率 | 1.000（11/11） | **0.9091（10/11，S5 FAIL）** | = 1.0 ❌ |
| 重复工单数 | 0 | **0**（S6 两次评估仅一张工单） | = 0 ✅ |
| 未授权提交数 | 0 | **0**（S7 注入/S10 越权/S11 未审批全部零副作用） | = 0 ✅ |
| 引用有效率（结构校验） | 1.000 | **1.000**（S1 引用全部解析到现行已批准 chunk） | = 1.0 ✅ |

- **S5 失败根因（如实记录，不改动门禁/场景）**：查询"设备报警了怎么处理"，
  词法 `can_answer=true`，DeepSeek 相关性复核判 `relevant=True`（checked=True，
  机制正常）→ `effective_can_answer=true` ≠ 场景期望。分析：该问题是**同域
  泛化问题**而非语义域外问题——"设备报警怎么处理"命中设备维护 SOP 从语义上
  是合理的（真实 LLM 判断正确）；M3 发现的"仅凭报警/处理得 0.287 词法分"
  风险在语义层面不成立。scripted 的"泛化词不计入"字符规则是比语义更严的口径。
  **结论**：LLM 相关性复核覆盖的是"语义域外"（菜谱/天气类必拒）；
  "同域泛化"问题的兜底属产品层（追问澄清/范围限定），列为后续改进项，
  本次不修改场景与门禁——评测的价值正是暴露这种口径差异；
- **机制验证结论**：安全门禁与真实 LLM 完全正交且全部有效——S7 语料注入
  零副作用、S10 越权审批 403、S11 未审批提交 409、S6 幂等（重复 0）、
  S1 引用有效率 1.0；诊断/相关性均为真实 LLM 输出且通过 grounding 守卫
  （引用越界即 FAILED_DIAGNOSIS_INVALID 的护栏未被触发亦未被绕过）；
- 已知观察：S11（纯工具层，无 LLM 调用）单次耗时 22.2s，疑为环境抖动
  （其余纯工具场景 0.1–0.2s），与 LLM 行为无关；
- **开发期发现并修复：环境变量泄漏进测试进程**——真实评测后本机持久化的
  `PMA_LLM_*` 用户级环境变量被 pytest 继承，`test_agent_api` 的 assess 走了
  真实 DeepSeek（套件耗时 22.5s→59.8s、2 个环境敏感断言失败）。根治：conftest
  增加 autouse `_force_offline_llm` 夹具，对全部测试强制离线 LLM 配置（需要
  传输细节的测试自行显式 monkeypatch 覆盖）；在密钥仍持久化的环境下重跑全量
  **222 passed / 22.3s、零网络调用**——"测试全离线"原则恢复并有机制保证；
- 密钥安全：`PMA_LLM_API_KEY` 仅存于会话/用户环境变量；报告与仓库不含密钥
  （提交前已全库检索验证）。**提醒**：该密钥曾在对话中明文出现，建议评测
  结束后在 DeepSeek 控制台轮换。



