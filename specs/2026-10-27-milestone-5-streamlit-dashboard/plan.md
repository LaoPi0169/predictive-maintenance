# M5 实施计划（plan.md）

任务按依赖排序，每组可独立测试。实现顺序 = 测试顺序 = 验收顺序。

## T1. 审计只读端点

- `app/main.py`：`GET /api/v1/agent-runs?asset_id=&limit=` →
  `list[AgentRunResult]`（repository.list_agent_runs 已有）；docstring 声明
  为 §8.3 之外的新增审计面。
- 测试：`tests/test_agent_runs_api.py`（空库返回 []、有运行返回完整
  AgentRunResult、asset_id 过滤、limit 上限）。

## T2. 看板 fetch 层（无 streamlit 依赖）

- `app/dashboard/backend.py`：
  - `DashboardClient(base_url)`（httpx）：`health / list_assets /
    latest_prediction / run_prediction / run_agent_assessment /
    list_work_orders / approve / submit / list_agent_runs / search_knowledge`；
  - 审批头注入（approver_id + role）；`DashboardError` 统一错误态
    （BACKEND_UNREACHABLE / HTTP_<code> + 服务端 detail 透传）；
  - 展示整形纯函数：`risk_badge(level)`（颜色/文案）、
    `compat_badge(status)`、`format_uncertainty(notes)`。
- 测试：`tests/test_dashboard_backend.py`（httpx ASGITransport 挂
  FastAPI——离线走完整 HTTP 栈；成功形状、404/409/403/503 错误态、
  后端不可达、头注入正确）。

## T3. Streamlit UI（薄壳）

- `app/dashboard/ui.py`：`import streamlit as st` 仅此一处；侧边栏
  （后端健康灯、页导航、审批身份表单）；四个页面渲染函数
  （风险看板 / 设备详情与维修建议 / 工单审核 / 操作日志），
  全部数据经 T2 fetch 层获取，错误态原样呈现；不写任何业务判断。
- 冒烟：模块可导入（无顶层 st 调用）；渲染逻辑不做自动化（人工验收）。

## T4. 端到端联调测试

- `tests/test_dashboard_e2e.py`：TestClient/ASGITransport + 种子库 +
  tmp 知识索引，经 fetch 层走完整业务流：预测评估 → Agent 评估出单 →
  工单列表查到 PENDING_APPROVAL → 审批 → 提交 → agent-runs 审计可查、
  tool trace 完整；含健康资产不出单分支。
- 这是「端到端联调」的自动化证明：UI 的全部数据来源都按此契约路径验证。

## T5. 收尾

- 全量 `python -m pytest` 记录真实数字；
- `requirements.txt`（streamlit==1.66.1 依赖注记）、README（两服务启动
  步骤 + 看板截图位/页面说明 + 路线图 M5 ✅）、CHANGELOG；
- validation.md 实测填写（测试数字、e2e 流程、人工验收清单）；
- git commit；已知局限如实记录。

## 交付文件清单

```
app/dashboard/{__init__,backend,ui}.py
app/main.py（扩展：/agent-runs）
scripts/（无新增；README 记录 streamlit run 命令）
tests/{test_agent_runs_api,test_dashboard_backend,test_dashboard_e2e}.py
specs/2026-10-27-milestone-5-streamlit-dashboard/
```
