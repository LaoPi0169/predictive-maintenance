# M2 需求规格：预测与风险（Prediction & Risk）

- 日期：2026-10-26
- 来源：`spec/Project.md`（§5 预测逻辑与风险引擎、§5.3 特征、§8.1 目录、
  §8.3/8.4 API 契约、§10.1 第二层模型测试、§10.2 验收指标）
- 工作流：`skills/rag-feature-spec`
- 上游：M1 数据地基（已交付：契约、合成数据、质量门禁、SQLite、/health、/assets）

## 1. 业务问题与用户

MVP 场景是离心泵（`PUMP_001`）轴承异常预测。M1 回答了"数据是否可信"；
M2 回答 spec §1.2 的第 1–2 步：**发现异常趋势（而不只是单次超限）并给出
可解释的风险等级与证据**。按 spec §5.1/§5.2，M2 采用**方案 A（规则 + 统计
异常检测）**：在只有合成退化标签、无真实故障样本时，先交付健康监测与风险
分级能力；有监督故障预测（方案 B）与 RUL（方案 C）延后。

主要用户：设备工程师（查看风险等级与证据）；M4 Agent（`get_prediction`
只读工具的数据来源）。

## 2. 范围

### In scope（本里程碑）

1. **时间窗口特征**（`app/data/features.py`，spec §5.3 + §8.1）：
   - 以 10 分钟为窗口聚合已接受读数，产出 spec §5.3 命名的特征：
     `vibration_mean_10m`、`vibration_std_10m`、`temperature_max_10m`、
     `temperature_slope_30m`、`current_mean_10m`、`load_pct`；
   - 振动/温度/电流/斜率仅在 **RUNNING** 读数上计算（spec §10.1 要求验证
     "不同运行负载下的表现"；停机/怠速窗口不得拉低基线制造虚假异常）；
     窗口记录 `n_readings`、`running_ratio`、`suspect_ratio`；
   - 读数不足的窗口显式跳过（计入统计），绝不插补、绝不虚构。
2. **统计异常检测 + 风险规则引擎**（`app/prediction/risk_rules.py`）：
   - 基线 = 该指标**历史开头的健康参考期**（前 N 个 RUNNING 可用窗口，
     严格早于被评估窗口 → 无泄漏）。不采用 trailing 滚动基线的原因：
     对持续上升的信号，trailing 基线自身方差随趋势增大，z 分数会饱和在
     ~√3 附近，恰好掩盖本里程碑必须检出的持续漂移（开发期实测缺陷，
     详见 validation.md）；
   - z 分数带每指标下限（std floor），防参考期方差过小虚高；
   - 复合 `anomaly_score` = 各指标 z 的最大值（封顶），输出逐指标
     `EvidenceItem`（复用 M1 契约，含 threshold_source）；
   - 风险四级 `NORMAL / WATCH / HIGH / EMERGENCY`（spec §5.4）：统计分数
     阈值、报警限值持续超限窗数**分开配置**（spec §5.4 明确要求分离）；
   - 全部阈值 `EXAMPLE_RULE` 标注，须设备工程师确认后方可用于真实设备。
3. **风险评估管线**（`app/prediction/predict.py`）：
   - `assess_asset(conn, asset_id, horizon_hours)`：读库 → 特征 → 证据 →
     风险 → 返回 `RiskAssessment`（新冻结契约，闭式 schema）并写入
     `predictions` 表留痕；
   - **诚实边界**：`confidence` 恒为 `None`（规则分数非校准概率，spec §3.2）；
     评估基于最后一个 RUNNING 主导窗口，记录其时间与新旧；
   - **失败状态**：数据不足（总读数或可用窗口低于下限）抛
     `InsufficientDataError` —— 绝不返回虚构的"低风险"（spec §8.4）；
     失败同样留痕（`risk_level=NULL`、`data_quality='INSUFFICIENT_DATA'`）。
4. **预测 API**（`app/main.py`，spec §8.3/8.4 契约）：
   - `POST /api/v1/predictions`，请求体 `{asset_id, horizon_hours∈[1,168]}`；
   - 200 `PredictionResponse`（`asset_id/risk_level/risk_score/model_version/
     data_quality` + 窗口边界与证据）；404 未知设备；**503 数据不足**
     （结构化错误体，区分"低风险"与"无法完成预测"）。
5. **predictions 表读写**（`app/database/repository.py`）：`insert_prediction` /
   `list_predictions`。
6. **基线评测脚本**（`scripts/evaluate_baseline.py`，spec 阶段二交付物）：
   - 多 seed 重放 normal / degradation 场景，对照 manifest `failure_time`
     真值，输出 `data/processed/model_evaluation.json`：
     已知故障检出率（目标示例 ≥85%，spec §10.2）、预警提前量、normal 场景
     HIGH+ 误报率（目标 0）、bad_data 场景的失败态行为。
7. **测试**：spec §10.1 第二层模型测试（正常/异常/已知故障/失败态）。

### Out of scope（后续里程碑）

- 有监督故障预测（方案 B，`train.py`）、RUL 估计（方案 C）——待真实标签；
- 逐设备健康端点 `/api/v1/assets/{id}/health`（§8.3 契约，M2 未列入路线图）；
- 维修知识 RAG（M3）、Agent 与工单（M4）、看板（M5）、ROI 对照（M6）。

## 3. 新增数据形状（契约摘要）

- `RiskAssessment`（闭式）：`asset_id`、`window_start/window_end`、
  `generated_at`、`horizon_hours`、`risk_level`、`risk_score`、
  `anomaly_score`、`risk_rule_version`、`model_version`、`confidence=None`、
  `evidence: list[EvidenceItem]`、`uncertainty_notes: list[str]`、
  `data_quality: OK|DEGRADED`。
- `PredictionRecord`（闭式，映射 `predictions` 表）：`asset_id`、
  `created_at`、`horizon_hours`、`window_start/window_end`、`risk_score`、
  `risk_level`、`model_version`、`data_quality`、`notes`。
- `PredictionResponse`（API，spec §8.4 字段超集，超集已在代码注释注明）。

## 4. 决策与理由

| 决策 | 理由 |
|---|---|
| 方案 A（规则+统计），不训练模型 | spec §5.2"第一版优先"；合成标签不足以支撑有监督方案，训练将是虚构精度 |
| 异常指标仅用 RUNNING 读数 | spec §10.1 要求按负载验证；混入怠速/停机会制造虚假 z 分数 |
| trailing 基线（仅用过去） | spec §5.3 数据泄漏警告；评估与在线使用同一代码路径 |
| z 分数带 std floor | 10 分钟均值方差极小，无下限时正常波动即触发高分（误报） |
| 风险分数 0–10 复合分 | 可解释、可配置；**不是概率**，故 `confidence=None` |
| 失败态写 predictions 留痕 | "无法评估"本身是必须可审计的事件，且防止下游把空结果当健康 |
| `train.py` 不交付 | 无可训练对象；空壳文件违背"不虚构"原则，延后至方案 B 规格 |
| 评测脚本独立于 seed_demo | seed_demo 是演示（单一场景）；评测需多 seed 批量重放与指标 |

## 5. 用户角色、权限与数据边界

- 只读评估 + 本地 predictions 写入，无外部副作用；无新增权限面（ACL 仍在 M4）；
- 所有评估仅针对 M1 质量门禁**接受**的数据；隔离数据不参与特征计算；
- 模拟数据的阈值与分数不代表真实设备行为（EXAMPLE_RULE）。

## 6. 预期失败状态与行为

| 失败状态 | 行为 |
|---|---|
| 数据库无该资产 | API 404，结构化错误体 |
| 读数不足 / 可用 RUNNING 窗口不足 | `InsufficientDataError` → API 503；predictions 留痕 `INSUFFICIENT_DATA`；绝不输出风险分数 |
| 窗口内 RUNNING 读数过少 | 该窗口跳过并计数，不插补 |
| 基线窗口不足 | 该指标无证据（不计入复合分），uncertainty_notes 说明 |
| 传感器质量差（suspect 占比高） | `data_quality=DEGRADED` + uncertainty note，照常给出保守证据 |
| 请求体非法（horizon 越界等） | 422（FastAPI 校验，契约闭式） |

## 7. 假设与未决问题

- **假设**：基线窗口数、z 阈值、持续超限窗数均为演示值（EXAMPLE_RULE），
  评测指标是"合成场景上的行为证明"，不是真实设备精度；
- **假设**：退化场景的 `failure_time` 真值来自生成器 manifest（M1 约定），
  检出定义为 failure_time 之前出现 HIGH/EMERGENCY；
- **未决**：误报率上限需"按设备和班次定义"（spec §10.2）——真实班次数据
  接入后才能标定，M2 仅报告合成场景误报数；
- **未决**：方案 B 的特征/标签管线与时间切分方案，在真实数据试点阶段另立规格。
