# Predictive Maintenance AI Agent (MVP)

设备预测性维护 AI Agent 的 MVP 实现。总体方案见 [`spec/Project.md`](spec/Project.md)；
功能规格遵循 `skills/rag-feature-spec` 的约定，按里程碑逐步交付。

> **当前状态：M3 — 维修知识 RAG（knowledge RAG）**
> 已交付：M1 数据地基（契约、合成数据、质量门禁、SQLite）+ M2 规则统计风险引擎
> （时间窗特征、异常检测、四级风险、预测 API、基线评测：检出率 1.0、提前量 ≈48h、健康场景 0 误报）
> + M3 知识检索（14 份演示语料入库、元数据过滤、带引用检索、评测集：Recall@5 = 1.0、
> 引用有效率 / 版本正确率 / 拒绝正确率 = 1.0）。
> 未交付：Agent 与工具调用（M4）、看板（M5）。

## 业务场景（MVP）

离心泵轴承异常预测：当振动、轴承温度或电机电流出现持续异常时，系统识别风险、
检索维修知识、生成待审核的维护工单草稿。所有数据为模拟数据，不对应真实设备。

## 技术栈

Python 3.11+（开发环境 3.13） · FastAPI · Pydantic v2 · Pandas · NumPy · SQLite · pytest
（M3 检索为**无新增依赖**的词法 TF-IDF 基线；chromadb/嵌入模型待审批后再引入）

## 快速开始（Windows PowerShell）

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt

# 生成演示数据并入库（确定性：固定 seed 可完整复现）
.venv\Scripts\python scripts\seed_demo.py

# 构建知识索引（语料 data/knowledge/*.md → data/processed/knowledge_index_*）
.venv\Scripts\python scripts\build_knowledge_index.py

# 运行检索评测（Recall@5 / MRR / 版本正确率 / 引用有效率 / 拒绝正确率）
.venv\Scripts\python scripts\evaluate_rag.py

# 运行基线评测（多 seed 退化检出率 / 提前量 / 误报，报告写入 data/processed/）
.venv\Scripts\python scripts\evaluate_baseline.py

# 启动 API
.venv\Scripts\python -m uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000/docs

# 运行测试
.venv\Scripts\python -m pytest
```

## 预测 API（M2）

```powershell
# 评估设备风险（成功 200；数据不足 503；未知设备 404；参数非法 422）
$body = '{"asset_id":"PUMP_001","horizon_hours":24}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/predictions `
    -ContentType "application/json" -Body $body
```

响应包含 `risk_level`（NORMAL/WATCH/HIGH/EMERGENCY）、`risk_score`（0–10 规则
复合分，**不是故障概率**，`confidence` 恒为 null）、`anomaly_score`、逐指标
`evidence`（含 threshold_source）与 `uncertainty_notes`。数据不足时返回 503 与
原因说明——系统绝不把"无法评估"伪装成"低风险"。每次评估（含失败态）都写入
`predictions` 表留痕，可用 `GET /api/v1/predictions` 查询。

## 知识检索 API（M3）

```powershell
# 知识检索（默认只返回已批准且现行版本的文档）
$body = '{"query":"轴承温度高怎么处理","top_k":5,"asset_model":"P-103"}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/knowledge/search `
    -ContentType "application/json; charset=utf-8" -Body $body
```

响应包含 `can_answer`、逐条 `results[].citation`（chunk_id / doc_id / 版本 / 章节 /
source_location / score）与命中原文。无可靠依据时 `can_answer=false` 且不返回引用——
**拒绝猜测**。索引缺失或语料变更后未重建索引时返回 503（`INDEX_MISSING` /
`INDEX_STALE`），绝不悄悄用旧知识回答。DRAFT 与 SUPERSEDED 文档默认不可见
（相似度不等于操作授权）。该端点是演示面；M4 Agent 将以库方式调用同一检索器。

## 项目结构

```
app/
├── main.py            # FastAPI 入口（/health、/assets、POST /predictions、POST /knowledge/search）
├── config.py          # 配置与示例阈值（EXAMPLE_RULE，需设备工程师确认）
├── schemas.py         # 冻结的 v1 数据契约（传感器数据 / 质量报告 / 风险评估 / 知识检索 / Agent 输出）
├── data/
│   ├── simulator.py   # 确定性合成数据：normal / degradation / bad_data 场景
│   ├── ingestion.py   # 数据质量门禁：校验、标记、隔离（坏数据绝不静默视为健康）
│   └── features.py    # 时间窗口特征（spec §5.3 命名，仅用 RUNNING 读数）
├── prediction/
│   ├── risk_rules.py  # 统计异常检测（健康参考期 z 分数）+ 四级风险规则引擎
│   └── predict.py     # 评估管线：读库 → 特征 → 风险 → 留痕；数据不足显式失败
├── knowledge/
│   ├── ingest_docs.py # 文档入库：元数据校验 → 结构分块 → TF-IDF 索引（确定性、幂等）
│   └── retriever.py   # 检索：元数据过滤（默认仅 APPROVED 现行版）+ 余弦 + 阈值拒绝
├── database/
│   ├── models.py      # SQLite 表结构（spec §8.2 的 8 张核心表）
│   └── repository.py  # 数据访问（assets / sensor_readings / predictions）
data/
├── raw/               # 合成原始数据 CSV + 生成 manifest（含 SHA-256）
├── knowledge/         # 演示知识库语料（14 份合成 Markdown，覆盖手册/SOP/案例/BOM）+ 评测集
└── processed/         # 校验后数据、隔离区、质量报告、模型与检索评测报告、知识索引
scripts/
├── seed_demo.py           # 一键演示数据入库
├── build_knowledge_index.py  # 知识索引构建 CLI
├── evaluate_baseline.py   # 基线评测：检出率 / 提前量 / 误报（spec §10.2 指标）
└── evaluate_rag.py        # 检索评测：Recall@5 / MRR / 引用 / 版本 / 拒绝（spec §7.3/§10.2）
tests/                 # 单元与集成测试（数据质量 + 模型 + RAG 三层测试体系）
specs/                 # 各里程碑的 feature 规格（requirements/plan/validation）
spec/Project.md        # 项目总方案（唯一事实来源）
skills/                # 工作流 skills（规格、工具集成、评测、changelog）
```

## 路线图

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 数据地基 | 数据契约、合成数据、校验、SQLite | ✅ |
| M2 预测与风险 | 时间窗口特征、异常检测、风险规则、预测 API | ✅ |
| M3 维修知识 RAG | 文档入库、元数据过滤、带引用检索、评测集 | ✅ 当前 |
| M4 Agent 与工具 | 6 个工具、状态机、服务端审批、幂等 | ⬜ |
| M5 前端集成 | Streamlit 看板、端到端联调 | ⬜ |
| M6 测试与验收 | 5 层测试体系、评测报告、试点计划 | ⬜ |

## 重要声明

- 所有传感器数值均为**确定性模拟数据**，仅用于演示与测试，不代表任何真实工厂测量值。
- `config.py` 中的阈值均标记为 `EXAMPLE_RULE` 占位值，**必须经设备工程师确认后**才能用于真实设备。
- 工单提交必须经过服务端审批校验（M4 实现）；Agent 不直接控制任何设备。
