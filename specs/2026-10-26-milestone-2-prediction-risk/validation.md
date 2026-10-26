# M2 验证计划（validation.md）

对应 spec §10.1 **第二层：模型测试**（正常/异常/已知故障案例、漏报、误报、
提前量、不同运行负载表现）与 §10.2 验收指标（已知故障召回率 ≥85% 为初始
目标示例、误报率设上限、提前量由维修准备时间确定）。

## 测试矩阵（规划）

| 用例组 | 断言 |
|---|---|
| 特征正确性 | 手工可算帧上的均值/标准差/斜率/窗口数/跳窗/占比全部吻合 |
| 平稳场景 | normal 数据评估为 NORMAL 或 WATCH，**绝不 HIGH/EMERGENCY** |
| 退化场景 | 故障前出现 HIGH/EMERGENCY（多 seed 检出率 ≥85% 初始目标），且时间早于 `failure_time`（提前量 > 0） |
| 持续超限 | 振动连续 ≥3 窗超报警限 → EMERGENCY（独立于统计分数的路径） |
| 诚实边界 | `confidence` 恒 None；证据 `threshold_source` 非空；基线不足 → 无该指标证据 + note |
| 失败态 | 读数不足 → `InsufficientDataError`；API 503 结构化错误体；未知资产 404；非法 horizon 422 |
| 留痕 | 成功与失败评估均写入 `predictions`；往返读写一致 |
| 数据质量 | suspect 占比超限 → `data_quality=DEGRADED` + note；bad_data 场景经门禁后不崩溃，隔离行绝不参与特征，suspect 占比如实上报（大部分行干净的时段给出 NORMAL 是诚实行为） |

## 运行命令

```powershell
.venv\Scripts\python -m pytest -v
.venv\Scripts\python scripts\evaluate_baseline.py
```

## Definition of Done

1. 第二层测试全部真实运行并通过；
2. 评测脚本在 ≥5 个 seed 上产出真实指标并写入本文件（不是估算值）；
3. API 四态（200/404/503/422）真实请求验证；
4. README 与实际命令一致；
5. CHANGELOG 增补并润色；
6. 已知局限如实记录（合成数据、EXAMPLE_RULE、无真实标签）。

---

## 实际验证结果

### 测试（2026-10-26 真实运行）

```
PS> .venv\Scripts\python -m pytest
collected 90 items

...
======================== 90 passed, 1 warning in 6.16s ========================
```

（M1 48 项 + M2 新增 42 项；唯一 warning 为 starlette TestClient 对 httpx 的
弃用提示，第三方依赖噪音。）

### 基线评测（scripts/evaluate_baseline.py，5 seeds × 4320 分钟 × 3 场景）

```
PS> .venv\Scripts\python scripts\evaluate_baseline.py
{
  "known_failure_detection_rate": 1.0,      <- 初始目标 ≥ 0.85（spec §10.2 示例值）
  "median_lead_minutes": 2889.0,            <- ≈ 48.2 h（72 h 退化全程）
  "min_lead_minutes": 2859.0,
  "normal_false_high_windows": 0,           <- 2097 个健康窗口，0 误报 HIGH+
  "normal_windows_scored": 2097
}
```

- 5/5 个 seed 全部在 `failure_time` 之前出现 HIGH/EMERGENCY，提前量
  2859–2959 分钟（47.7–49.3 h），高度一致；
- 健康场景 5 个 seed 合计 2097 个评估窗：0 个 HIGH/EMERGENCY，
  WATCH 合计 13 个（统计噪声的如实上报，不作为告警依据）；
- bad_data 场景：每 seed 20 行被 M1 门禁隔离（不参与特征），29–37 个
  窗口因 suspect 占比被如实标记 `data_quality=DEGRADED`，无崩溃。

### 开发期实测缺陷（均已修复并回归）

1. **pandas 时间单位错乱**：tz-aware Series `.astype("int64")` 在新 pandas
   下不再保证纳秒，`searchsorted` 全部越界 → 斜率恒 NaN。改为显式
   epoch 秒（`_epoch_seconds`）。
2. **trailing 基线 z 饱和**：对持续上升的信号，滚动基线自身方差随趋势
   增大，z 渐近 ~√3，实测 `max_anomaly_score=2.9`，退化检不出来。改为
   "历史开头健康参考期"基线（`ANOMALY_REFERENCE_WINDOWS=72`，严格早于
   被评估窗 → 无泄漏），检出任一 seed。
3. **斜率规则误报**：温度残差含负载耦合噪声（σ≈1.44 ℃/读数），30 分钟
   OLS 斜率噪声 σ≈1.8 ℃/h，6 ℃/h 报警线仅 3.3σ → 健康数据出现 5 个
   虚假 HIGH。措施：斜率回看窗要求整段 RUNNING（状态切换 → NaN 不虚构），
   下限/报警线抬至 3.0/10.0 ℃/h 与实测噪声匹配；斜率降级为 WATCH 级
   辅助证据。
4. **响应模型类型错误**：`PredictionResponse.evidence` 声明为
   `list[dict[str, str]]` 而 `observed_value` 是 float → pydantic 校验失败。
   改为直接复用 `EvidenceItem` 契约。

### API 冒烟（真实 uvicorn 进程，对 seed_demo 建库的 4320 行退化数据）

```
GET  /health                -> {"status":"ok","version":"0.2.0","contract_version":"1.0"}
POST /api/v1/predictions    -> level=EMERGENCY score=9.9 anomaly=9.9
                               model=rules-stat-v1 quality=OK evidence_n=6
GET  /api/v1/predictions    -> 1 行审计留痕（risk_level=EMERGENCY）
```

（演示库数据止于故障时刻，评估返回 EMERGENCY 符合预期：振动 7.37 mm/s
与温度 79.8 ℃ 均已持续越限。）

### 产物清单（已确认存在）

- `data/processed/model_evaluation.json`（评测完整报告，含逐 seed 明细）
- `specs/2026-10-26-milestone-2-prediction-risk/`（规格三件套）
- 新增模块：`app/data/features.py`、`app/prediction/{risk_rules,predict}.py`、
  `scripts/evaluate_baseline.py`

## Definition of Done

| 项 | 状态 |
|---|---|
| 第二层测试（正常/异常/已知故障/失败态/留痕） | ✅ 90/90 通过 |
| 评测脚本多 seed 真实指标写入本文件 | ✅ 5 seeds，检出率 1.0、提前量 ≈48 h、误报 0 |
| API 四态（200/404/503/422）真实验证 | ✅ TestClient + 真实 uvicorn 冒烟 |
| README 与实际命令一致 | ✅ 已更新（M2 命令与 API 示例） |
| CHANGELOG 增补并润色 | ✅ |
| 已知局限如实记录 | ✅ 见下 |

### 已知局限（如实声明）

- 所有指标基于**确定性合成数据**（sim-v1），非真实测量；检出率/提前量
  证明的是引擎在已知坡度退化场景上的行为，不是真实设备精度；
- 全部阈值为 `EXAMPLE_RULE`（分数阈值、参考期长度、std floor、斜率线），
  上线前须由设备工程师确认；
- "健康参考期"假设历史开头 12 h 为正常运行——真实接入时须由人工指定
  或验证参考期；
- 无校准概率（`confidence` 恒 null）、无有监督模型（方案 B 待真实标签）、
  无 RUL；误报率上限的"按设备与班次定义"需真实班次数据（spec §10.2）。
