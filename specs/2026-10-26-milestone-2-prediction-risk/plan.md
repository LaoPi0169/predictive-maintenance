# M2 实施计划（plan.md）

任务按依赖排序，每组可独立测试。实现顺序 = 测试顺序 = 验收顺序。

## T1. 配置与契约扩展

- `app/config.py`：特征窗口参数（窗口 10min、斜率窗 30min、窗口最小读数）、
  异常检测参数（基线窗数、最小基线、各指标 std floor、分数封顶）、
  风险阈值（WATCH/HIGH/EMERGENCY 分数、持续超限窗数、斜率关注阈值）、
  评估下限（最小总读数、最小可用窗口数、DEGRADED suspect 占比）、
  `PREDICTION_MODEL_VERSION` / `RISK_RULE_VERSION`。全部 `EXAMPLE_RULE` 注释。
- `app/schemas.py`：新增 `RiskAssessment`、`PredictionRecord`（闭式 schema）。
- 测试：`tests/test_schemas.py` 追加（未知字段拒绝、非法枚举、缺字段）。

## T2. 时间窗口特征

- `app/data/features.py`：
  - `build_feature_windows(readings: pd.DataFrame, ...) -> pd.DataFrame`：
    输入已排序的**已接受**读数；输出每窗口一行（spec §5.3 特征命名）+
    `n_readings`/`running_ratio`/`suspect_ratio`；
  - `temperature_slope_30m`：窗口末端向前 30min 线性拟合，单位 ℃/h；
  - RUNNING 子集计算振动/温度/电流/斜率；窗口读数 < 下限时跳过（计数）；
  - `windows_since` 辅助：保证只依赖过去数据。
- 测试：`tests/test_features.py`（手工可算的小帧：均值/标准差/斜率符号与
  数值、窗口数、跳窗计数、suspect 占比、仅 RUNNING 计算生效）。

## T3. 统计异常检测 + 风险规则引擎

- `app/prediction/__init__.py`、`app/prediction/risk_rules.py`：
  - `score_window(features_row, history: pd.DataFrame) -> RiskAssessment`：
    trailing 基线 z 分数（每指标 floor、封顶）→ 逐指标 EvidenceItem →
    复合 anomaly_score → 风险四级（统计阈值 + 持续超限两条独立路径）；
  - 基线不足的指标不产生证据、计入 uncertainty_notes；
  - 输出永远携带 `confidence=None` 与 `risk_rule_version`。
- 测试：`tests/test_risk_rules.py`（平稳数据 NORMAL；注入持续抬升 →
  z 增长且等级单调不降；振动持续超限 → EMERGENCY 路径；基线不足 →
  INSUFFICIENT 证据缺失 + note；证据 threshold_source 非空）。

## T4. 评估管线与存储留痕

- `app/prediction/predict.py`：
  - `assess_asset(conn, asset_id, horizon_hours)`：`get_readings` →
    `build_feature_windows` → `score_window`（最新可用窗口）→
    `RiskAssessment`；`InsufficientDataError` 当总读数/可用窗口不足；
  - `persist_assessment`：写 `predictions`（成功与失败态都留痕）。
- `app/database/repository.py`：`insert_prediction`、`list_predictions`。
- 测试：`tests/test_prediction.py` + `tests/test_repository.py` 追加
  （留痕往返、失败态留痕、空库抛 `InsufficientDataError`）。

## T5. API 与评测脚本

- `app/main.py`：`POST /api/v1/predictions`（§8.4 契约；404/503/422/200
  四态；响应 = `PredictionResponse`，字段为 §8.4 的超集并在注释注明）
  与 `GET /api/v1/predictions`（审计留痕查询，暴露失败态记录）。
- `scripts/evaluate_baseline.py`：多 seed × {degradation, normal} 重放 →
  门禁 → 特征 → 逐窗评估 → 对照 `failure_time` 真值 →
  `data/processed/model_evaluation.json`（检出率/提前量/误报数）。
- 测试：`tests/test_api.py` 追加（200 含留痕、404、503、422；tmp DB 隔离）；
  `tests/test_evaluation.py`（小规模单 seed 指标结构健全、bad_data 不产生
  "干净 NORMAL"）。

## T6. 收尾

- 全量 `python -m pytest`，记录真实结果；
- 运行 `scripts/evaluate_baseline.py`（seed 42/7/123/2026/999 × 3 天），
  把真实指标写入 validation.md；
- 更新 README（路线图状态、M2 命令、API 示例）；
- git commit；changelog 脚本 + 润色；已知局限如实记录。

## 交付文件清单

```
app/data/features.py
app/prediction/{__init__,risk_rules,predict}.py
app/database/repository.py（扩展）
app/{schemas,config,main}.py（扩展）
scripts/evaluate_baseline.py
tests/{test_features,test_risk_rules,test_prediction,test_evaluation}.py
tests/{test_schemas,test_repository,test_api}.py（扩展）
specs/2026-10-26-milestone-2-prediction-risk/
```
