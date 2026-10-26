# M1 需求规格：数据地基（Data Foundation）

- 日期：2026-10-26
- 来源：`spec/Project.md`（§3 数据契约、§8.1 目录结构、§8.2 数据库表、§10.1 第一层数据质量测试）
- 工作流：`skills/rag-feature-spec`
- 上游路线图：spec §四 的阶段一（业务定义与数据准备）

## 1. 业务问题与用户

MVP 场景为离心泵（`PUMP_001`）轴承异常预测。在任何预测、诊断或工单生成发生之前，
系统必须具备可靠的数据地基：统一的数据契约、可复现的合成数据源、以及不被"坏数据
静默解释为健康设备"的数据质量门禁。

主要用户：后续里程碑的开发者与评测者；最终用户为设备工程师（当前里程碑不直接交付 UI）。

## 2. 范围

### In scope（本里程碑）

1. **冻结 v1 数据契约**（Pydantic v2，闭式 schema）：
   - `SensorReading`：spec §3.1 的传感器数据字段（timestamp、site_id、asset_id、
     operating_state、load_pct、vibration_mm_s、bearing_temp_c、motor_current_a、
     pressure_bar、sensor_quality）；
   - `DataQualityReport` / `DataQualityIssue`：数据质量报告与问题分类；
   - `Asset`：设备主数据；
   - `AgentAssessment` 及其子结构：spec §3.2 的 Agent 标准输出契约（后续里程碑使用，
     本里程碑仅冻结结构）。
2. **确定性合成数据生成器**（`app/data/simulator.py`）：
   - 场景：`normal`（健康运行）、`degradation`（轴承退化直至已知故障时刻）、
     `bad_data`（注入缺值/重复/乱序/断连/尖峰/单位错误/质量坏标记）；
   - 固定 seed 完全可复现；输出 CSV + 生成 manifest（含 SHA-256、再生成命令）；
   - `degradation` 场景记录已知 `failure_time` 与 `failure_mode`，作为 M2 评估的 ground truth。
3. **数据质量门禁**（`app/data/ingestion.py`）：
   - 检测：必需列缺失、时间戳非法、重复记录、乱序时间戳、时间线断连（gap）、
     必需值缺失、物理范围越界、尖峰嫌疑、单位错误嫌疑、枚举非法、源质量标记 BAD；
   - 处理：硬问题行进入隔离区（quarantine），软问题行保留但强制标记 `SUSPECT`；
     所有决策记录进 `DataQualityReport`；
   - 核心原则：**未被明确接受的数据绝不参与下游，绝不静默视为健康**。
4. **SQLite 存储**（`app/database/`）：
   - spec §8.2 的 8 张核心表 DDL（assets、sensor_readings、failure_events、
     predictions、maintenance_cases、inventory、work_orders、agent_runs）；
   - M1 仅实现 assets / sensor_readings 的读写；sensor_readings 以
     `UNIQUE(asset_id, ts)` 保证幂等写入。
5. **最小 FastAPI 入口**（`app/main.py`）：`/health` 与 `GET /api/v1/assets`。
6. **测试**（第一层数据质量测试体系）与 README、manifest。

### Out of scope（后续里程碑）

- 时间窗口特征、异常检测模型、风险规则引擎、预测 API（M2）
- 知识库 RAG、检索评测（M3）
- Agent 状态机、6 个工具、审批与幂等工单（M4）
- Streamlit 看板（M5）
- 5 层测试体系的第 2–5 层、ROI 评估（M6）

## 3. 数据形状（契约摘要）

- 传感器数据：见 spec §3.1 JSON；UTC ISO-8601 时间戳；闭式 schema 拒绝未知字段。
- Agent 标准输出：见 spec §3.2 JSON；`confidence` 在无校准模型时必须为 `null`；
  `requires_human_approval` 默认 `true`。
- 质量报告：total_rows / accepted_rows / quarantined_rows / issue_counts / issues[]，
  且 `accepted + quarantined == total`（不变量，测试断言）。

## 4. 决策与理由

| 决策 | 理由 |
|---|---|
| Python 3.13（spec 建议 3.11+） | 环境现状；pandas≥2.2.3 / numpy≥2.1 / pydantic≥2.9 均已支持 |
| sqlite3 标准库 + 显式 DDL，不引入 ORM | MVP 最小依赖；表结构与 spec §8.2 一一对应，生产迁 PostgreSQL 时契约不变 |
| 隔离区用文件（data/processed/quarantine/）而非新表 | spec §8.2 未定义隔离表；文件产物便于人工复核与测试断言 |
| fastapi/uvicorn 提前到 M1 | spec §8.1 的目录结构包含 main.py；/health 让"新开发者可运行"尽早成立 |
| 合成数据时间起点固定（2026-10-01T00:00:00Z） | 保证同 seed 下 CSV 字节级可复现（哈希可测试） |
| 尖峰检测按 operating_state 分组计算滚动中位数 | 避免将运行状态切换（如停机）误报为尖峰 |
| 代码/注释英文，文档中文 | 与 spec 的 System Prompt（英文）及团队文档（中文）习惯一致 |

## 5. 用户角色、权限与数据边界

- 本里程碑无写操作、无外部副作用，仅本地文件与本地 SQLite；
- 模拟数据不包含任何真实客户、工厂或设备信息（spec 明确要求）；
- 角色与 ACL 在 M4（工单审批）落地；本里程碑不虚构权限模型。

## 6. 预期失败状态与行为

| 失败状态 | 行为 |
|---|---|
| 输入 CSV 缺必需列 | 抛出 `DataContractError`，不产出任何"部分成功"的静默结果 |
| 行级坏数据 | 隔离 + 报告记录；接受集只含通过校验的行 |
| 时间线 gap | 信息性问题记录（数据缺失≠坏数据），不隔离任何行 |
| 数据库未初始化 | `connect()` 自动执行 `CREATE TABLE IF NOT EXISTS` |
| API 查询空库 | 返回空列表，不报错、不虚构数据 |

## 7. 假设与未决问题

- **假设**：示例数值（振动基线 2.5 mm/s、轴承温度基线 55 ℃ 等）为演示值；
  `config.py` 阈值标记 `EXAMPLE_RULE`，上线前须由设备工程师确认。
- **假设**：单位错误检测采用启发式（轴承温度 > 100 ℃ 判为可疑、> 110 ℃ 判为越界），
  仅适用于当前演示场景。
- **未决**：M4 的 LLM 供应商与编排选型（倾向轻量 Python 状态机，见 spec 建议）；
  在 M3/M4 规格中再决定。
- **未决**：生产环境的租户/工厂权限、审计日志字段——spec §8.2 要求，延后到 M4+ 设计。
