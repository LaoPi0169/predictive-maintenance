# M6 需求规格：测试与验收（5 层测试体系、评测报告、试点计划）

- 日期：2026-10-27
- 来源：`spec/Project.md`（§10.1 五层测试体系、§10.2 MVP 验收指标、§10.3 业务价值
  衡量、§13 交付物清单 test-report.md）
- 工作流：`skills/rag-evaluation`（确定性代码做门禁，LLM-as-judge 只作辅助）
- 上游：M1–M5 全部交付物与其评测产物（model/rag/agent evaluations）

## 1. 业务问题与用户

M1–M5 的能力与测试散落在各里程碑；spec §10 要求以**五层测试体系**和
**§10.2 验收指标**收口，并给出**试点计划**。M6 是路线图收官：把分散的
评测产物聚合为一份可机读的验收摘要，把测试体系映射成可审计的测试报告，
把"接入真实设备"写成可执行的试点计划——不做新功能。

主要用户：设备/维修工程师（第五层人工验收、试点执行）；项目评审者
（测试报告与指标对照）。

## 2. 范围

### In scope（本里程碑）

1. **评测聚合器**（`scripts/collect_metrics.py`）：读取
   `data/processed/` 的全部评测产物（model / rag / agent 基线 /
   agent 真实 LLM〔如存在〕），输出 **`data/processed/evaluation_summary.json`**：
   - 每个产物的门禁状态与关键指标；
   - **§10.2 验收指标对照表**：每条指标的目标 / 实测 / 状态
     （PASS | FAIL | INFO〔信息性〕| MISSING〔需真实数据〕）/ 来源；
   - 缺失产物显式列出（绝不静默跳过）；
   - 总体门禁：全部 PASS 才算通过（MISSING 的业务价值类指标不计入失败，
     但必须显式出现在"试点期采集"清单）。
2. **测试报告**（`docs/test-report.md`，spec §13 交付物）：五层测试体系
   映射——每层 → 测试文件 → 用例数 → 运行命令 → 状态；§10.2 对照表
   （引用 evaluation_summary 实测值）；第五层人工验收清单（设备工程师）；
   真实运行数字。
3. **试点计划**（`docs/pilot-plan.md`）：影子运行（shadow mode）方案——
   范围与故障模式、数据接入要求、运行程序、验证指标（§10.2 阈值在真实
   数据上重校准）、角色职责、时间表、退出/回滚标准、已知限制。
4. **聚合器测试**：`tests/test_collect_metrics.py`（正常聚合、缺失产物、
   门禁失败传导、§10.2 状态判定、真实 LLM 报告的非确定性标注）。

### Out of scope（spec §13 其余交付物，需真实数据/部署后）

- `roi-report.md`（停机/MTBF/MTTR 需试点期真实测量，§10.3：无对照组与
  历史基线不得宣称收益）；`deployment-runbook.md`（生产部署属 §十一，
  试点验证后）；`data-dictionary.xlsx`（数据字典已由冻结契约 + README
  覆盖，Excel 版本按需）。

## 3. 新增数据形状

- `evaluation_summary.json`（机读，非冻结契约——内部报告）：
  `{generated_at, artifacts: {...}, acceptance: [{metric, target, measured,
  status, source}], missing: [...], overall_passed}`。
- 无 API/数据库/契约变更；无新依赖。

## 4. 决策与理由

| 决策 | 理由 |
|---|---|
| 聚合器只读产物、不做评测 | 各评测已自带门禁与测试；聚合器职责是单一事实来源的汇总，重复评测只会制造不一致 |
| §10.2 对照表进 JSON + 人读 MD 双载体 | JSON 供机器/后续工具；test-report.md 供评审；两者数字同源（聚合器输出） |
| 业务价值指标标记 MISSING 而非 omit | spec §10.3：无对照/基线不得宣称收益——诚实呈现"待试点采集"比留空更可审计 |
| 真实 LLM 报告单独标注（deterministic=false） | DeepSeek 重跑 10/11（S5 口径发现）是有效证据但不改变基线门禁 |
| 五层映射为人工维护文档 | 层→文件的归属是工程判断，自动推断会脆；用条目数与命令保证可复核 |
| 试点计划写影子运行（shadow mode） | spec §十二/§十一：先只读验证再授权写入；Agent 不是安全仪表系统 |

## 5. §10.2 验收指标对照（设计态）

| 指标 | 目标 | 实测来源 | 状态 |
|---|---|---|---|
| 必填字段校验覆盖率 | 100% | 冻结闭式契约 + L1 测试 | PASS |
| 坏数据识别与隔离 | 测试 100% 通过 | L1 测试 + 基线评测 bad_data_runs（每 run 隔离 20 行） | PASS |
| 已知故障召回率 | ≥ 85% | model_evaluation：1.0（5 seed 退化回放全部检出） | PASS |
| 误报率 | 按设备/班次设上限 | 健康回放 false HIGH = 0 / 2097 窗口 | PASS |
| 预测提前量 | 由维修准备时间确定 | 中位 2889 min ≈ 48.2 h（信息性） | INFO |
| Recall@5 | ≥ 90% | rag_evaluation：1.0 | PASS |
| 关键维修建议有有效依据 | 100% | agent_evaluation.citation_validity = 1.0（结构校验） | PASS |
| 工单必填字段校验 | 100% | 闭式契约 + 业务校验门 | PASS |
| 未经授权的提交 | 0 次 | agent_evaluation.unauthorized_submits = 0 | PASS |
| 重复事件重复工单 | 0 次 | agent_evaluation.duplicate_work_orders = 0 | PASS |
| 非计划停机 / MTBF / MTTR | 与基线对比 | 需试点期真实测量 | MISSING |

## 6. 预期失败状态与行为

| 失败状态 | 行为 |
|---|---|
| 产物文件缺失 | `missing` 列表 + 该指标 MISSING；必需产物缺失 → overall_failed |
| 门禁失败（任一 PASS 指标不达标） | overall_failed=true，退出码 1 |
| 真实 LLM 报告存在 | 并列展示并标注 `deterministic: false`，不替代基线门禁 |
| 业务价值指标 | 恒 MISSING（试点期采集），不计入失败 |

## 7. 假设与未决问题

- **假设**：评测产物由各里程碑命令生成于本机（gitignored）；聚合器跨机器
  使用时需先重跑各评测命令；
- **假设**：试点计划的阈值重校准责任人为设备工程师（EXAMPLE_RULE 约定延续）；
- **未决**：试点用真实 CMMS/ historians 的接入方式（试点计划列出候选与
  决策点，不预设）。
