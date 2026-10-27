"""Streamlit dashboard UI (M5) — a thin rendering shell over
``app/dashboard/backend.py`` (spec/Project.md §四 阶段05 + §8.5).

Rules of this module:

- every number/text comes from the backend via ``DashboardClient`` — no
  local business logic, no fabricated data; ``DashboardError`` is rendered
  as an explicit error (server reason included), never swallowed;
- the approval identity is the demo-grade declared identity from M4: the
  role must be on the server-side allowlist, the server still validates
  everything (``X-Approver-Id`` / ``X-Approver-Role`` headers);
- run with:  .venv\\Scripts\\streamlit run app\\dashboard\\ui.py
  (FastAPI must be running: python -m uvicorn app.main:app --port 8000)
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run app/dashboard/ui.py` does NOT put the project root on
# sys.path (unlike `python -m ...`), so bootstrap it before any `app` import.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from app import config  # noqa: E402
from app.dashboard.backend import (  # noqa: E402
    DashboardClient,
    DashboardError,
    compat_badge,
    format_uncertainty,
    risk_badge,
    workorder_badge,
)


PAGE_TITLES = {
    "风险看板": "设备风险总览",
    "设备详情与维修建议": "预测评估与 Agent 维修建议",
    "工单审核": "维护工单审批与提交",
    "操作日志": "Agent 运行 / 预测 / 工单审计",
}


@st.cache_resource
def get_client() -> DashboardClient:
    """One client per Streamlit session (base URL from PMA_API_BASE_URL)."""
    return DashboardClient()


def render_sidebar(client: DashboardClient) -> str:
    """Health light + page navigation + approver identity; returns page key."""
    st.sidebar.title("🔧 预测性维护")
    try:
        health = client.health()
        st.sidebar.success(f"后端在线 · v{health.get('version', '?')}")
    except DashboardError as exc:
        st.sidebar.error(f"后端不可达（{exc.code}）\n\n{exc}")
        st.sidebar.caption(f"API 地址：{config.API_BASE_URL}")

    page = st.sidebar.radio("页面", list(PAGE_TITLES), key="page")

    st.sidebar.divider()
    st.sidebar.subheader("审批身份（演示级声明）")
    st.sidebar.caption("角色必须在服务端白名单内；服务端仍校验工单状态机。")
    st.sidebar.selectbox("角色", list(config.APPROVER_ROLES), key="approver_role")
    st.sidebar.text_input("审批人 ID", value="engineer-1", key="approver_id")
    return page


def _guard(render_fn, client: DashboardClient) -> None:
    """Render a page; surface DashboardError explicitly instead of hiding it."""
    try:
        render_fn(client)
    except DashboardError as exc:
        detail_text = str(exc.detail) if exc.detail else ""
        st.error(f"**[{exc.code}]** {exc}\n\n{detail_text}")


# ------------------------------------------------------------- 风险看板 ----


def render_risk_overview(client: DashboardClient) -> None:
    st.header("风险看板")
    assets = client.list_assets()
    if not assets:
        st.info("数据库中没有设备。先运行 scripts/seed_demo.py 生成演示数据。")
        return

    counts: dict[str, int] = {}
    rows = []
    for asset in assets:
        latest = client.latest_prediction(asset.asset_id)
        level = latest.risk_level if latest else None
        counts[level or "NO_DATA"] = counts.get(level or "NO_DATA", 0) + 1
        rows.append(
            {
                "设备": asset.asset_id,
                "型号": asset.model,
                "重要度": asset.criticality,
                "最新风险": risk_badge(level),
                "风险分": latest.risk_score if latest else None,
                "数据质量": latest.data_quality if latest else "—",
                "评估时间": latest.created_at.strftime("%m-%d %H:%M") if latest else "—",
            }
        )
    cols = st.columns(len(counts) or 1)
    for col, (level, count) in zip(cols, sorted(counts.items())):
        col.metric(risk_badge(level) if level != "NO_DATA" else "⚪ 无数据", count)
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption("风险分为规则复合分（0–10，非故障概率）；数据来自 predictions 审计表。")


# ------------------------------------------------- 设备详情与维修建议 ----


def render_asset_detail(client: DashboardClient) -> None:
    st.header("设备详情与维修建议")
    assets = [a.asset_id for a in client.list_assets()]
    if not assets:
        st.info("数据库中没有设备。")
        return
    asset_id = st.selectbox("设备", assets, key="detail_asset")

    left, right = st.columns(2)
    if left.button("运行预测评估", key="btn_predict"):
        result = client.run_prediction(asset_id)
        if result.get("risk_level"):
            st.success(
                f"{risk_badge(result['risk_level'])}  ·  risk_score={result['risk_score']}  ·  "
                f"model={result['model_version']}"
            )
            st.write("**证据**")
            st.json(result["evidence"])
            st.write("**不确定性说明**")
            st.markdown(format_uncertainty(result["uncertainty_notes"]))
        else:
            st.warning(
                f"无法可靠评估：{result.get('data_quality')}\n\n"
                + format_uncertainty(result.get("uncertainty_notes"))
            )

    if right.button("运行 Agent 维护评估", key="btn_agent", type="primary"):
        with st.spinner("Agent 运行中（预测 → 知识检索 → 诊断 → 备件 → 工单）…"):
            run = client.run_agent_assessment(asset_id)
        if run.status.value != "COMPLETED" or run.assessment is None:
            st.error(f"运行失败：{run.status.value}\n\n{run.failure_reason}")
        else:
            assessment = run.assessment
            st.success(
                f"{risk_badge(assessment.risk_level.value)}  ·  run={run.run_id[:8]}  ·  "
                f"LLM={run.model_provider}"
            )
            if assessment.failure_mode_candidates:
                st.write("**故障模式候选**（置信度恒为 null——未经校准不得虚构概率）")
                for candidate in assessment.failure_mode_candidates:
                    st.markdown(f"- {candidate.mode} · confidence={candidate.confidence}")
                    if candidate.rationale:
                        st.caption(candidate.rationale)
            st.write("**建议动作**")
            for action in assessment.recommended_actions:
                st.markdown(f"- {action}")
            if assessment.knowledge_sources:
                st.write("**知识依据（chunk）**")
                st.code(", ".join(assessment.knowledge_sources))
            if assessment.parts_requirements:
                st.write("**备件需求**")
                for part in assessment.parts_requirements:
                    st.markdown(
                        f"- {compat_badge(part.compatibility_status)} {part.part_name}"
                        + (f"（{part.part_id}）" if part.part_id else "")
                        + f" × {part.quantity}"
                    )
            st.info(
                f"工单状态：{workorder_badge(assessment.work_order_status.value)} · "
                f"需要人工审批：{'是' if assessment.requires_human_approval else '否'}"
                "（审批在「工单审核」页进行）"
            )
            st.write("**不确定性说明**")
            st.markdown(format_uncertainty(assessment.uncertainty_notes))
            with st.expander("工具调用轨迹"):
                st.json([t.model_dump(mode="json") for t in run.tool_trace])

    st.divider()
    st.subheader("知识库检索")
    query = st.text_input("查询", key="kb_query", placeholder="如：轴承温度高怎么处理")
    if st.button("检索", key="btn_kb") and query:
        retrieval = client.search_knowledge(query, asset_model="P-103")
        if retrieval.can_answer:
            for hit in retrieval.results:
                st.markdown(
                    f"- `{hit.citation.chunk_id}` **{hit.citation.section_title}**"
                    f"（{hit.citation.doc_id} v{hit.citation.doc_version}，score="
                    f"{hit.citation.score:.3f}）"
                )
                st.caption(hit.text)
        else:
            st.warning(f"无可靠依据，拒绝猜测：{retrieval.insufficient_reason}")


# ------------------------------------------------------------- 工单审核 ----


def render_work_orders(client: DashboardClient) -> None:
    st.header("工单审核")
    approver_id = st.session_state.get("approver_id", "")
    approver_role = st.session_state.get("approver_role")
    st.caption(f"当前审批身份：{approver_id or '（未填）'} / {approver_role}")

    status = st.selectbox(
        "状态过滤",
        ["", "PENDING_APPROVAL", "APPROVED", "REJECTED", "SUBMITTED"],
        format_func=lambda s: workorder_badge(s) if s else "全部",
        key="wo_filter",
    )
    orders = client.list_work_orders(status=status or None)
    if not orders:
        st.info("没有符合条件的工单。先在「设备详情」页运行 Agent 评估。")
        return

    rows = [
        {
            "工单": wo.work_order_id,
            "设备": wo.asset_id,
            "状态": workorder_badge(wo.status.value),
            "优先级": wo.priority or "—",
            "审批人": wo.approved_by or "—",
            "外部引用": wo.external_ref or "—",
            "创建": wo.created_at.strftime("%m-%d %H:%M"),
        }
        for wo in orders
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    work_order_id = st.selectbox(
        "选择工单", [wo.work_order_id for wo in orders], key="wo_select"
    )
    selected = next(wo for wo in orders if wo.work_order_id == work_order_id)
    with st.expander("工单内容", expanded=True):
        st.write(selected.problem_description)
        st.write("**建议任务**")
        for task in selected.recommended_tasks:
            st.markdown(f"- {task}")
        if selected.parts_requirements:
            st.write("**备件**")
            for part in selected.parts_requirements:
                st.markdown(
                    f"- {compat_badge(part.compatibility_status)} {part.part_name} × {part.quantity}"
                )

    st.radio("审批决定", ["APPROVE", "REJECT"], key="wo_decision", horizontal=True)
    col_a, col_s = st.columns(2)
    if col_a.button("应用审批决定", key="btn_approve", type="primary"):
        decision = st.session_state.get("wo_decision", "APPROVE")
        record = client.approve(work_order_id, decision, approver_id, approver_role)
        st.success(f"已应用：{workorder_badge(record.status.value)}（approved_by={record.approved_by}）")
    if col_s.button("提交到 CMMS", key="btn_submit"):
        record = client.submit(work_order_id)
        st.success(
            f"已提交：{workorder_badge(record.status.value)}  ·  external_ref={record.external_ref}"
        )
    st.caption("审批/提交在服务端校验角色白名单与状态机；403/409 会在此页显示原因。")


# ------------------------------------------------------------- 操作日志 ----


def render_operations_log(client: DashboardClient) -> None:
    st.header("操作日志")
    runs = client.list_agent_runs(limit=20)
    st.subheader(f"Agent 运行（最近 {len(runs)} 次）")
    if not runs:
        st.info("尚无 Agent 运行记录。")
    for run in runs:
        head = (
            f"{run.started_at.strftime('%m-%d %H:%M:%S')} · {run.asset_id} · "
            f"{run.status.value} · LLM={run.model_provider} · run={run.run_id[:8]}"
        )
        with st.expander(head):
            if run.failure_reason:
                st.error(run.failure_reason)
            if run.assessment is not None:
                a = run.assessment
                st.write(
                    f"风险 {risk_badge(a.risk_level.value)} · 工单 "
                    f"{workorder_badge(a.work_order_status.value)} · "
                    f"知识源 {len(a.knowledge_sources)} 条"
                )
            st.write("**工具轨迹**")
            st.json([t.model_dump(mode="json") for t in run.tool_trace])

    st.subheader("预测审计（含失败态留痕）")
    predictions = client.list_predictions(limit=20)
    st.dataframe(
        [
            {
                "设备": p.asset_id,
                "时间": p.created_at.strftime("%m-%d %H:%M:%S"),
                "风险": risk_badge(p.risk_level),
                "风险分": p.risk_score if p.risk_score is not None else "—",
                "数据质量": p.data_quality,
                "说明": p.notes[:60],
            }
            for p in predictions
        ],
        use_container_width=True,
        hide_index=True,
    )


    st.subheader("工单记录")
    orders = client.list_work_orders(limit=20)
    st.dataframe(
        [
            {
                "工单": wo.work_order_id,
                "设备": wo.asset_id,
                "状态": workorder_badge(wo.status.value),
                "幂等键": (wo.idempotency_key or "")[:12],
                "审批人": wo.approved_by or "—",
                "外部引用": wo.external_ref or "—",
            }
            for wo in orders
        ],
        use_container_width=True,
        hide_index=True,
    )


_PAGE_RENDERERS = {
    "风险看板": render_risk_overview,
    "设备详情与维修建议": render_asset_detail,
    "工单审核": render_work_orders,
    "操作日志": render_operations_log,
}


def main() -> None:
    st.set_page_config(page_title="预测性维护看板", page_icon="🔧", layout="wide")
    client = get_client()
    page = render_sidebar(client)
    st.title(PAGE_TITLES[page])
    _guard(_PAGE_RENDERERS[page], client)


if __name__ == "__main__":
    main()



