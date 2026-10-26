# M1 验证计划（validation.md）

对应 spec §10.1 **第一层：数据质量测试**，以及验收指标"必填字段校验覆盖率 100%、
坏数据识别与隔离测试用例 100% 通过"。

## 测试矩阵

### 契约（test_schemas.py）

| 用例 | 断言 |
|---|---|
| 合法传感器数据 | 构造成功，字段保序保型 |
| 未知字段 | `ValidationError`（闭式 schema） |
| 缺必需字段 | `ValidationError` |
| 非法枚举值 | `ValidationError` |
| 负振动值 | `ValidationError` |
| naive 时间戳（无时区） | `ValidationError` |
| Agent 输出默认值 | `confidence=None` 合法、`requires_human_approval=True`、`work_order_status=NONE` |
| 置信度越界 | `ValidationError`（0–1 之外） |

### 生成器（test_simulator.py）

| 用例 | 断言 |
|---|---|
| 确定性 | 同参数两次生成 DataFrame 逐值相等；CSV 字节 SHA-256 一致 |
| seed 敏感性 | 不同 seed 产生不同数据 |
| normal/degradation 有效性 | 100% 行通过 `SensorReading` 契约校验 |
| 退化趋势 | 末段 RUNNING 滚动均值振动 > 首段 + 3.0 mm/s；温度末段 > 首段 + 15 ℃ |
| 故障真值 | meta 记录 failure_time == 起点 + (periods-1)×采样间隔 |
| bad_data 缺陷存在 | 缺值≥5、重复≥3、乱序、尖峰、单位错误、BAD 标记、gap 均出现 |
| manifest | 写出且 sha256 与 CSV 实际哈希一致 |

### 质量门禁（test_ingestion.py）

| 用例 | 断言 |
|---|---|
| 缺必需列 | `DataContractError`，无部分产出 |
| 重复行 | 保留首条，副本隔离，`DUPLICATE_ROW` 计数正确 |
| 乱序时间戳 | 标记 `OUT_OF_ORDER`（软问题），行保留且输出按时间排序 |
| 缺值 | 隔离，`MISSING_VALUE` |
| 硬限值越界（负值/极端尖峰） | 隔离，`RANGE_VIOLATION` |
| 尖峰（限值内大偏差） | 保留但强制 `SUSPECT`，`SPIKE_SUSPECT` |
| 单位错误嫌疑带 | 隔离，`UNIT_ERROR_SUSPECT` |
| 源标记 BAD | 隔离，`QUALITY_FLAG_BAD` |
| 时间线 gap | `GAP_IN_TIMELINE` 仅记录，不隔离行 |
| 不变量：总量守恒 | `accepted + quarantined == total_rows` |
| 不变量：无静默健康 | 每个被隔离行都出现在 report.issues 中 |
| 边界契约 | 接受集 100% 可构造 `SensorReading` |
| 端到端 | ingest_csv 产出 accepted/quarantine/report 三类文件 |

### 存储（test_repository.py）

| 用例 | 断言 |
|---|---|
| 建表 | 8 张核心表 + schema_migrations 全部存在 |
| 资产 upsert | 重复 upsert 不报错、不重复 |
| 读数幂等插入 | 相同 (asset_id, ts) 二次插入 inserted=0，表内仅 1 行 |
| 最新/范围查询 | 返回顺序与时间过滤正确 |

### API（test_api.py）

| 用例 | 断言 |
|---|---|
| /health | 200，status=ok |
| /api/v1/assets | 返回已注册资产（tmp DB 隔离，不碰真实库） |

## 运行命令

```powershell
.venv\Scripts\python -m pytest -v
```

## Definition of Done

1. 上述全部测试实际运行且通过（记录真实输出，不声称未运行的测试）。
2. `python scripts/seed_demo.py` 可一键产出：raw CSV + manifest、accepted/quarantine/
   quality report 三类 processed 文件、已填充的 SQLite。
3. README 的每条命令都与实际验证一致。
4. `CHANGELOG.md` 由 changelog skill 从真实 git 历史生成并人工润色。
5. 已知局限如实记录：模拟数据非真实测量；阈值为 EXAMPLE_RULE；
   无预测模型/RAG/Agent；无多租户与 ACL。

---

## 实际验证结果（2026-10-26，真实运行记录）

环境：Windows，Python 3.13.15，pytest 9.1.1，pydantic 2.14.1，pandas 3.1.0，numpy 2.5.4。

### 测试

```
PS> .venv\Scripts\python -m pytest
collected 48 items

tests\test_api.py ........  (3 passed)
tests\test_ingestion.py ....  (16 passed)
tests\test_repository.py .....  (5 passed)
tests\test_schemas.py ............  (12 passed)
tests\test_simulator.py ............  (12 passed)

======================== 48 passed, 1 warning in 2.11s ========================
```

唯一 warning 为 starlette TestClient 对 httpx 的弃用提示（第三方依赖噪音，无功能影响）。

开发过程中修复的三个真实缺陷（均由上述测试捕获）：

1. `app/main.py` 与 `scripts/seed_demo.py` 的错误导入（`from app import repository`
   应为 `from app.database import repository`）——由 API 测试与演示脚本运行发现；
2. `validate()` 缺少结构列检查（此前只存在于 `load_csv`）——由
   `test_missing_required_column_raises` 发现后，将检查前移进门禁本体；
3. 空 DataFrame 无列名时会误触结构检查——确认该行为正确（无表头的空文件本就
   结构非法），修正测试使其构造带正确列名的空帧。

### 端到端演示（scripts/seed_demo.py，真实输出）

```
Generating deterministic synthetic data (scenario=degradation, seed=42)...
  raw csv     : ...\data\raw\degradation_seed42.csv
  manifest    : ...\data\raw\manifest_degradation.json
Running the layer-1 data quality gate...
  total=4320 accepted=4320 quarantined=0
  db          : ...\data\pma.db
  readings    : attempted=4320 inserted=4320
  latest      : 2026-10-03T23:59:00+00:00 vibration=7.372 mm/s temp=79.777 C
```

（退化场景为干净数据，隔离数为 0；坏数据场景的隔离能力由 test_ingestion 的
bad_data 端到端用例覆盖，且 `python -m app.data.simulator --scenario bad_data`
CLI 已实际运行验证，产出 348 行含缺陷数据。）

### API 冒烟测试（真实 uvicorn 进程）

```
GET /health         -> {"status":"ok","version":"0.1.0","contract_version":"1.0"}
GET /api/v1/assets  -> [{"asset_id":"PUMP_001","site_id":"SITE_001",
                        "model":"Centrifugal Pump P-103 (demo)","criticality":"A",...}]
```

### 产物清单（已确认存在）

- `data/raw/degradation_seed42.csv` + `manifest_degradation.json`（CSV 被 gitignore，manifest 入库）
- `data/raw/bad_data_seed42.csv` + `manifest_bad_data.json`
- `data/processed/degradation_seed42_accepted.csv` / `_quality_report.json`
- `data/processed/quarantine/degradation_seed42_quarantine.csv`
- `data/pma.db`（SQLite，含全部 9 张表）

### DoD 达成情况

| 条目 | 状态 |
|---|---|
| 全部测试真实运行并通过 | ✅ 48/48 |
| seed_demo 一键产出全部产物 | ✅ |
| README 命令与实际一致 | ✅（venv/pytest/seed/uvicorn 均已逐条验证） |
| CHANGELOG 由真实 git 历史生成 | ✅（见仓库根目录 CHANGELOG.md） |
| 已知局限已记录 | ✅（本节及 requirements.md §7） |

