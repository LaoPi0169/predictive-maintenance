# 测试报告（Test Report）— 五层测试体系

- 项目：设备预测性维护 AI Agent（MVP，M1–M5 已交付）
- 日期：2026-10-27 · 总用例：**247**（`python -m pytest`，全离线确定性）
- 依据：`spec/Project.md` §10.1 五层测试体系、§10.2 MVP 验收指标
- 指标对照的机读版：`data/processed/evaluation_summary.json`
  （`python scripts/collect_metrics.py` 生成）

## 总览

| 层 | 内容 | 状态 |
|---|---|---|
| 1 | 数据质量测试 | ✅ |
| 2 | 模型测试（正常/异常/已知故障、漏报/误报/提前量） | ✅ |
| 3 | RAG 与诊断测试（检索/引用/过期过滤/拒绝猜测） | ✅ |
| 4 | Agent 与工具异常路径测试 | ✅ |
| 5 | 业务验收测试（人工，设备工程师） | ⬜ 待试点执行（清单见 §五） |

## 第一层：数据质量测试

坏数据绝不被静默解释为健康设备（spec §10.1）。

| 测试文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/test_schemas.py` | 36 | 冻结闭式契约（未知字段拒绝、非法枚举、边界、伪造审批字段拒绝） |
| `tests/test_simulator.py` | 12 | 确定性合成数据：契约有效性、确定性、退化趋势越限、故障时刻记录 |
| `tests/test_ingestion.py` | 16 | 缺失值/重复/乱序/断连/单位错误/尖峰的识别与隔离；坏数据 → 隔离区 |
| `tests/test_repository.py` | 8 | 持久层幂等与审计回读 |

## 第二层：模型测试

| 测试文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/test_features.py` | 9 | 时间窗特征（spec §5.3 命名、仅 RUNNING 读数） |
| `tests/test_risk_rules.py` | 9 | 统计异常基线 + 四级风险规则 |
| `tests/test_prediction.py` | 6 | 评估管线：数据不足显式失败并留痕 |
| `tests/test_evaluation.py` | 4 | 基线评测门禁（多 seed 回放） |
| 基线评测产物 | — | `data/processed/model_evaluation.json`：**召回率 1.0**（≥85% 目标）、**健康回放 false HIGH = 0 / 2097 窗口**、提前量中位 2889 min ≈ 48.2 h、bad_data 每 run 隔离 20 行 |

运行：`python scripts/evaluate_baseline.py`

## 第三层：RAG 与诊断测试

| 测试文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/test_knowledge_ingest.py` | 12 | 元数据校验 fail-fast、结构分块确定性、语料哈希 |
| `tests/test_retriever.py` | 14 | 元数据过滤、SUPERSEDED/DRAFT 不可见、低于阈值拒绝、引用可回溯 |
| `tests/test_knowledge_api.py` | 6 | 200/422/503 INDEX_MISSING/INDEX_STALE |
| `tests/test_rag_evaluation.py` | 4 | 30 题评测门禁：**Recall@5 = 1.0**（≥90%）、版本/引用/拒绝正确率 = 1.0 |
| M4 相关性复核 | — | 泛化词域外拒绝（scripted）；真实 LLM 下"同域泛化"口径差异已记录（见 `specs/2026-10-26-milestone-4-agent-tools/validation.md`） |

运行：`python scripts/build_knowledge_index.py && python scripts/evaluate_rag.py`

## 第四层：Agent 与工具异常路径测试

异常路径覆盖：**工具失败轨迹、库存不可查（UNKNOWN→UNAVAILABLE）、工单重复提交
（幂等键 0 重复）、审批拒绝/越权 403、LLM 服务不可用显式降级/失败留痕、提示注入
零副作用、知识索引缺失降级**（spec §10.1 第四层）。

| 测试文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/test_agent_llm.py` | 15 | LLM 端口：三 provider、相关性契约、失败语义 |
| `tests/test_llm_transport.py` | 10 | 真实端点传输层（mock 服务器）：重试/回退/密钥不外泄 |
| `tests/test_workorder_service.py` | 13 | 幂等、状态机、白名单、服务端审批复核 |
| `tests/test_agent_tools.py` | 14 | 六工具契约 + 轨迹审计 + 信任边界 |
| `tests/test_agent_graph.py` | 9 | 有界状态机：出单/不出单/失败态/降级/注入零副作用 |
| `tests/test_agent_api.py` | 5 | assess 端点错误矩阵 |
| `tests/test_workorder_api.py` | 8 | 审批/提交端点错误矩阵 |
| `tests/test_agent_evaluation.py` | 4 | 11 场景评测门禁：通过率 1.0、重复 0、越权提交 0、引用有效率 1.0 |
| `tests/test_llm_transport.py`（真实运行） | — | DeepSeek `deepseek-chat` 同套重跑 10/11（S5 口径发现，详见 M4 validation） |

运行：`python scripts/evaluate_agent.py`（`--provider openai_compatible` 可用真实 LLM 重跑）

## 第五层：业务验收测试（人工，设备工程师）

自动化只能证明结构与门禁；以下清单需设备工程师在试点/演示中逐项确认
（结果记录到试点日志）：

- [ ] 诊断与维修建议审核：对 ≥3 次真实（或回放）高风险事件，确认故障模式候选、
      建议动作与知识引用"符合现场经验"；
- [ ] 工单字段审核：问题描述、建议任务、备件需求、优先级完整且可执行；
- [ ] 备件兼容性核对：CONFIRMED 件与现场 BOM 一致；PENDING 件的处理路径明确；
- [ ] 审批记录核对：approved_by / approved_at / external_ref 与管理流程一致；
- [ ] 维护结果反馈：工单闭环（实际维修结果）回填机制的可行性确认；
- [ ] 阈值确认：EXAMPLE_RULE（报警限值、可操作风险级别、审批角色）签字。

## §10.2 验收指标对照

与 `evaluation_summary.json` 同源（`python scripts/collect_metrics.py` 生成，
当前 `overall_passed: true`）：

| 指标 | 目标 | 实测 | 状态 |
|---|---|---|---|
| 必填字段校验覆盖率 | 100% | 闭式契约 + L1 | ✅ PASS |
| 坏数据识别与隔离 | 测试 100% | 每 run 隔离 20 行 + L1 | ✅ PASS |
| 已知故障召回率 | ≥ 85% | 1.0 | ✅ PASS |
| 误报率（false HIGH） | 设上限 | 0 / 2097 窗口 | ✅ PASS |
| 预测提前量 | 按维修准备时间 | 中位 ≈ 48.2 h | ℹ️ INFO |
| Recall@5 | ≥ 90% | 1.0 | ✅ PASS |
| 关键建议有有效依据 | 100% | 1.0（结构校验） | ✅ PASS |
| 工单必填字段 | 100% | 闭式契约 + 校验门 | ✅ PASS |
| 未经授权提交 | 0 | 0 | ✅ PASS |
| 重复事件重复工单 | 0 | 0 | ✅ PASS |
| 停机 / MTBF / MTTR | 与基线对比 | 待试点采集 | ⬜ MISSING |

诚实声明：以上全部基于**确定性合成数据**（simulator sim-v1），证明的是管线
正确性与门禁有效性；真实设备上的指标须按 `docs/pilot-plan.md` 影子运行重校准。
所有阈值（报警限值、可操作级别、审批角色）为 EXAMPLE_RULE，需设备工程师确认。
