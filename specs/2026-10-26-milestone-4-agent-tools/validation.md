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
