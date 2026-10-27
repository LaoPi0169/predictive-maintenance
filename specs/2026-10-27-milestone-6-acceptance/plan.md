# M6 实施计划（plan.md）

## T1. 评测聚合器

- `scripts/collect_metrics.py`：
  - `collect(data_dir) -> summary`：读 model/rag/agent(基线)/agent(真实 LLM，可选)
    产物 → artifacts 摘要 + §10.2 验收对照（PASS/FAIL/INFO/MISSING）+
    missing 列表 + overall_passed；
  - `main()`：写 `data/processed/evaluation_summary.json`，打印对照表，
    退出码 0/1。
- 测试：`tests/test_collect_metrics.py`（tmp 目录夹具产物：正常聚合、
  缺失必需产物、召回率不达标传导 FAIL、真实 LLM 报告标注、业务价值 MISSING）。

## T2. 测试报告（docs/test-report.md）

- 五层映射：每层 → 覆盖内容 → 测试文件 → 用例数（pytest --collect 实数）→
  运行命令 → 状态；
- §10.2 对照表（与 evaluation_summary 同源数字）；
- 第五层人工验收清单（设备工程师签字栏）；
- 真实运行记录（240 passed 等）与已知局限。

## T3. 试点计划（docs/pilot-plan.md）

- 影子运行方案：范围（单台离心泵/轴承故障模式）、数据要求（≥2 周历史 +
  实时接入候选）、运行程序（只读评估、Agent 建议不提交、人工对照）、
  验证指标与重校准（§10.2）、角色职责、时间表（周粒度）、退出/回滚标准、
  已知限制与生产化前提（§十一）。

## T4. 收尾

- 运行 `scripts/collect_metrics.py` 生成真实 summary；
- 全量 pytest 记录真实数字；
- README（路线图 M6 ✅ + docs 链接）、CHANGELOG；
- validation.md 实测填写；git commit。

## 交付文件清单

```
scripts/collect_metrics.py
docs/{test-report,pilot-plan}.md
tests/test_collect_metrics.py
specs/2026-10-27-milestone-6-acceptance/
```
