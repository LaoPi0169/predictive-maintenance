# M6 验证计划（validation.md）

## 1. 测试与验证项

1. 聚合器：正常聚合（四产物齐全）、缺失必需产物（overall_failed）、
   门禁失败传导（召回率 <0.85 → FAIL）、真实 LLM 报告非确定性标注、
   业务价值指标恒 MISSING 不计失败；
2. §10.2 对照表：目标/实测/状态与 evaluation_summary 同源一致；
3. 全量 pytest 保持通过（回归）。

## 2. 命令

```bash
python -m pytest
python scripts/collect_metrics.py
```

## 3. Definition of Done

- [ ] 聚合器测试通过且数字为真实运行结果；
- [ ] evaluation_summary.json 落盘且 §10.2 全部指标有状态（无静默缺项）；
- [ ] docs/test-report.md 五层映射完整（层→文件→用例数→命令→状态）；
- [ ] docs/pilot-plan.md 含影子运行程序、验证指标、退出/回滚标准；
- [ ] README / CHANGELOG 更新；业务价值指标 MISSING 状态如实声明。

## 4. 已知局限（如实声明）

- 评测产物 gitignored，跨机器需重跑各评测命令；
- 业务价值（停机/MTBF/MTTR/ROI）需试点期真实数据，当前为 MISSING；
- 第五层（人工验收）依赖设备工程师执行，文档只提供清单与签字栏。

---

## 实际验证结果（2026-10-27 实测）

### 测试汇总
- `.venv\Scripts\python -m pytest` → **247 passed**（M1–M5 回归 240 + 聚合器 7）。

### 聚合器真实运行（`python scripts/collect_metrics.py` →
`data/processed/evaluation_summary.json`）
- **overall_passed = true**；§10.2 十一行全部有状态：
  - PASS ×9：字段校验（闭式契约）、坏数据隔离（每 run 20 行）、召回率 1.0
    （≥85%）、误报 0/2097、Recall@5 1.0（≥90%）、建议依据 1.0、工单字段、
    未授权提交 0、重复工单 0；
  - INFO ×1：预测提前量中位 2889 min ≈ 48.2 h（min 2859）；
  - MISSING ×1：业务价值（停机/MTBF/MTTR）——待试点采集，不计失败、不虚构；
- 真实 LLM 报告并列展示（deterministic=false，10/11），基线门禁以
  scripted 报告为准。

### 文档交付
- `docs/test-report.md`：五层映射（L1 数据质量 72 项 / L2 模型 36 项 / L3 RAG 36 项 /
  L4 Agent 与工具 78 项 / 端到端与基础设施 33 项（M5 e2e、/agent-runs、聚合器等）/
  L5 人工清单）+ §10.2 对照 + 诚实声明（合成数据、EXAMPLE_RULE）；总数 247 与
  pytest 实际运行一致；
- `docs/pilot-plan.md`：影子运行 4–6 周程序、数据要求、成功门槛、退出/回滚。

### DoD 核对
- [x] 聚合器测试通过（7 项）且数字为真实运行结果
- [x] evaluation_summary.json 落盘，§10.2 全指标有状态（无静默缺项）
- [x] 五层映射完整（层→文件→用例数→命令→状态）
- [x] 试点计划含影子运行程序、验证指标、退出/回滚标准
- [x] README / CHANGELOG 更新；业务价值 MISSING 如实声明

