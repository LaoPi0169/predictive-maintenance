# M1 实施计划（plan.md）

任务按依赖排序，每组可独立测试。实现顺序 = 测试顺序 = 验收顺序。

## T1. 配置与契约

- `app/config.py`：路径、数据库位置、`EXAMPLE_RULE` 阈值、校验参数
  （硬限值表、单位错误嫌疑带、尖峰绝对偏差、采样间隔、gap 倍数）。
- `app/schemas.py`：`SensorReading`、`OperatingState`、`SensorQuality`、
  `RiskLevel`、`WorkOrderStatus`、`FailureModeCandidate`、`EvidenceItem`、
  `PartRequirement`、`AgentAssessment`、`DataQualityIssue`、`IssueType`、
  `DataQualityReport`、`Asset`、`CONTRACT_VERSION`。
  - 全部闭式 schema（`extra="forbid"`）；时间戳必须带时区。
- 测试：`tests/test_schemas.py`（合法/未知字段/缺字段/非法枚举/越界/naive 时间戳）。

## T2. 合成数据生成器

- `app/data/simulator.py`：
  - `generate(scenario, seed, periods, freq_minutes)` → `(DataFrame, meta)`；
  - 场景 normal / degradation（含已知 failure_time）/ bad_data（七类缺陷注入）；
  - `save_csv` / `write_manifest`（SHA-256 + 再生成命令）；
  - CLI：`python -m app.data.simulator --scenario degradation --seed 42`。
- 测试：`tests/test_simulator.py`（确定性、seed 敏感性、场景有效性、缺陷存在性、
  normal/degradation 100% 通过契约校验）。

## T3. 数据质量门禁

- `app/data/ingestion.py`：
  - `load_csv` → `validate` → `IngestionResult(accepted, quarantined, report)`；
  - `to_readings(df)`：接受集边界处强制过一遍 Pydantic 契约；
  - `ingest_csv(path, output_dir)`：写出 accepted/quarantine CSV + 质量报告 JSON。
  - 检查优先级：结构错误（缺列）→ 抛异常；行级：时间戳 → 重复 → 乱序 → 排序 →
    枚举 → 缺值 → 硬限值 → 单位错误 → 尖峰（按状态分组滚动中位数）→ 源质量标记 → gap。
- 测试：`tests/test_ingestion.py`（每类缺陷一个用例 + 不变量
  `accepted + quarantined == total` + "无静默健康"不变量 + 端到端文件产出）。

## T4. 存储层

- `app/database/models.py`：8 张表 DDL + `schema_migrations`；`init_db(path)`。
- `app/database/repository.py`：`connect`（自动建表）、`upsert_asset`、
  `insert_readings`（幂等）、`get_latest_reading`、`get_readings`、`list_assets`。
- 测试：`tests/test_repository.py`（建表齐全、幂等插入、时间范围查询）。

## T5. API 入口与演示脚本

- `app/main.py`：`/health`、`GET /api/v1/assets`（spec §8.3 契约的 M1 子集）。
- `scripts/seed_demo.py`：建库 → 注册 PUMP_001 → 生成 degradation 数据 →
  质量门禁 → 入库 → 打印摘要。
- 测试：`tests/test_api.py`（health、assets 列表，DB 用 tmp_path 隔离）。

## T6. 收尾

- 全量 `python -m pytest`，记录真实结果；
- 更新 README（以实际命令为准）；
- git commit；运行 `python skills/changelog/scripts/changelog.py` 生成并润色 `CHANGELOG.md`；
- 在本目录记录验证结果与已知局限（见 validation.md 的 DoD）。

## 交付文件清单

```
app/{__init__,config,schemas,main}.py
app/data/{__init__,simulator,ingestion}.py
app/database/{__init__,models,repository}.py
scripts/seed_demo.py
tests/{test_schemas,test_simulator,test_ingestion,test_repository,test_api}.py
data/raw/（manifest 提交，CSV 生成物不入库）
```
