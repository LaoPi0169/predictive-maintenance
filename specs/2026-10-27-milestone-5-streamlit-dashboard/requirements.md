# M5 需求规格：Streamlit 看板与端到端联调（Dashboard & E2E）

- 日期：2026-10-27
- 来源：`spec/Project.md`（§2.1 前端=Streamlit「快速开发可演示的维护看板」、
  §四 阶段05「风险看板、设备详情、维修建议、工单审核和操作日志，交付可现场
  演示的 MVP」、§8.3 API 契约、§8.5「前端按契约开发 + pytest 契约测试完成集成」、
  §8.1 目录 `frontend/dashboard.py`）
- 工作流：`skills/rag-feature-spec`（契约先行）+ `skills/agent-tool-integration`
  （审批身份/信任边界延续 M4）
- 上游：M1–M4（数据地基、预测 API、知识检索 API、Agent assess 与工单四端点）

## 1. 业务问题与用户

M1–M4 的能力全部通过 API 暴露，但没有可现场演示的界面。M5 交付 Streamlit
看板，把「风险 → 证据 → 维修建议 → 工单 → 审批 → 审计」的闭环呈现给
维修/设备工程师，并完成**端到端联调**：看板作为唯一面向用户的入口，
经 HTTP 走 FastAPI 契约（spec §8.5），自动测试覆盖同样的契约路径。

## 2. 范围

### In scope（本里程碑）

1. **看板 fetch 层**（`app/dashboard/backend.py`，无 streamlit 依赖）：
   `DashboardClient`（httpx）封装 §8.3 全部已交付端点——assets、
   predictions（GET/POST）、maintenance/assess、work-orders（GET/POST）、
   approve/submit（含 `X-Approver-Id/X-Approver-Role` 头）、knowledge/search、
   **新增 `GET /api/v1/agent-runs`**；统一错误态
   `DashboardError(BACKEND_UNREACHABLE | HTTP_<code>)`——后端不可达时显式
   报错，绝不显示假数据。
2. **新增 API**：`GET /api/v1/agent-runs?asset_id=&limit=`（只读审计面，
   §8.3 表之外的声明新增，先例 /health 与 /knowledge/search）——操作日志页
   需要 agent_runs（run 状态 + tool trace）。
3. **Streamlit UI**（`app/dashboard/ui.py`，薄壳）：侧边栏页导航 + 后端
   健康指示灯 + 审批身份（角色下拉=服务端白名单、审批人 ID 输入），
   四个页面：
   - **风险看板**：设备列表、各设备最新风险等级/分数/数据质量（颜色徽章）、
     风险分布统计；
   - **设备详情与维修建议**：选设备 → 触发预测评估（POST /predictions）→
     一键 Agent 评估（POST /maintenance/assess）→ 展示风险证据、故障模式
     候选（置信度 null 必须如实显示）、建议动作与知识引用、备件需求三态、
     工单状态与「待人工审批」横幅、不确定性说明；
   - **工单审核**：工单列表（状态过滤）→ 审批（APPROVE/REJECT）→ 提交；
     403/404/409 错误态显示服务端原因；
   - **操作日志**：agent_runs（run 状态/provider/逐工具轨迹展开）、
     predictions 审计（含失败态）、work_orders 记录。
4. **端到端联调测试**（pytest，spec §8.5 的契约集成方式）：httpx
   `ASGITransport` 直接挂 FastAPI 应用（无网络、走完整 HTTP 栈），
   对 fetch 层驱动**完整业务流**：种子数据 → 预测 → Agent 评估出单 →
   审批 → 提交 → 审计日志可查；并覆盖后端不可达/404/409/503 错误态。
5. **依赖**：`streamlit==1.66.1`（路线图既定选型，§2.1）。

### Out of scope（后续里程碑）

- 登录会话与真实权限（沿用演示级声明式头）；图表美化与多设备/多工厂
  视图；自动刷新/WebSocket 实时流；M6 的 5 层测试体系收口、ROI 对照、
  试点计划；部署容器化。

## 3. 新增数据形状

- 无新契约：看板是 §8.3 既有契约的**消费者**；新增仅
  `GET /api/v1/agent-runs` → `list[AgentRunResult]`（M4 已冻结模型）。
- `DashboardError`（看板侧）：`code: BACKEND_UNREACHABLE | HTTP_404 | ...`、
  `detail`（服务端错误体原样透传，供 UI 显示原因）。

## 4. 决策与理由

| 决策 | 理由 |
|---|---|
| 看板经 HTTP 调 FastAPI，而非库内直调 | spec §8.5「前端按契约开发 + pytest 契约测试完成集成」+ 阶段05「端到端联调」本意：UI 与后端以 §8.3 契约为唯一耦合面，联调即验证契约 |
| fetch 层（backend.py）与 UI（ui.py）分离，backend.py 不 import streamlit | Streamlit 难以单测；把全部可测逻辑（请求、错误态、数据整形）压进 fetch 层用 pytest 全覆盖，UI 保持薄壳 |
| httpx `ASGITransport` 挂 FastAPI 做联调测试 | 无需真实端口/进程即可走完整 HTTP 栈（头、状态码、错误体），离线确定性 |
| 新增 `GET /api/v1/agent-runs` | 操作日志页需要 tool trace 审计；M4 已落库 `agent_runs`，缺只读面；声明式新增（先例 /health、/knowledge/search） |
| 审批身份用侧边栏声明（角色下拉=白名单 + ID 输入） | 与 M4 演示级身份一致；服务端白名单/状态机校验不变，UI 只是头部的来源 |
| 风险徽章/文案在 backend 层整形（如 `risk_badge()`） | 颜色/文案映射可单测，UI 不含业务逻辑 |
| 不做自动刷新/实时流 | MVP 演示手动刷新足够；实时推送属生产化范围 |

## 5. 用户角色、权限与边界

- 沿用 M4 演示级声明式身份：侧边栏选择的角色必须在服务端
  `APPROVER_ROLES` 白名单内，审批/提交失败（403/409）原样展示服务端原因；
- 看板是只读 + 受控写（assess/approve/submit）的薄客户端：不做任何本地
  业务判断，不缓存授权状态；
- 后端不可达/索引缺失/数据不足 → 各页面显示服务端错误体或明确提示，
  绝不显示编造数据。

## 6. 预期失败状态与行为

| 失败状态 | 行为 |
|---|---|
| FastAPI 未启动 | 侧边栏健康灯红 + 页面 `BACKEND_UNREACHABLE` 提示（含 base_url），不渲染假数据 |
| 未知资产（404） | 详情页显示 `ASSET_NOT_FOUND` |
| 数据不足（503 CANNOT_ASSESS） | 显示失败态与原因（AgentRunResult.failure_reason），区分「无法评估」与「低风险」 |
| 越权审批（403）/状态机错误（409） | 工单页红条显示服务端 reason |
| 知识索引缺失/过期（503 INDEX_*） | Agent 评估按 M4 契约降级（uncertainty note），页面如实显示 |

## 7. 假设与未决问题

- **假设**：演示时后端（uvicorn）与看板（streamlit）分别启动，README 提供一键步骤；
- **假设**：单用户演示场景，Streamlit rerun 模型足够（无并发写冲突——写路径
  幂等且服务端校验）；
- **未决**：M6 是否为看板补充冒烟级 UI 自动化（Playwright）；生产部署形态
  （容器化/反代）。
