# Predictive Maintenance AI Agent (MVP)

设备预测性维护 AI Agent 的 MVP 实现。总体方案见 [`spec/Project.md`](spec/Project.md)；
功能规格遵循 `skills/rag-feature-spec` 的约定，按里程碑逐步交付。

> **当前状态：M6 — 测试与验收（路线图全部交付 ✅）**
> 已交付：M1 数据地基（契约、合成数据、质量门禁、SQLite）+ M2 规则统计风险引擎
> （时间窗特征、异常检测、四级风险、预测 API、基线评测：检出率 1.0、提前量 ≈48h、健康场景 0 误报）
> + M3 知识检索（14 份演示语料入库、元数据过滤、带引用检索、评测集：Recall@5 = 1.0、
> 引用有效率 / 版本正确率 / 拒绝正确率 = 1.0）
> + M4 Agent（LangGraph 有界状态机、spec §6.3 六工具、LLM 相关性复核、备件三态、
> 幂等工单、服务端审批门禁、注入防护；11 场景评测全过；真实 LLM（DeepSeek）接入与同套重跑 10/11）
> + M5 看板（Streamlit 四页经 §8.3 HTTP 契约端到端联调，真实 uvicorn 全业务流自动化验证）
> + M6 验收（五层测试体系收口 `docs/test-report.md`、§10.2 指标对照
> `data/processed/evaluation_summary.json`、影子运行试点计划 `docs/pilot-plan.md`）。
> 后续：按 `docs/pilot-plan.md` 接入真实设备影子运行（需设备团队执行）。

## 业务场景（MVP）

离心泵轴承异常预测：当振动、轴承温度或电机电流出现持续异常时，系统识别风险、
检索维修知识、生成待审核的维护工单草稿。所有数据为模拟数据，不对应真实设备。

## 技术栈

Python 3.11+（开发环境 3.13） · FastAPI · Pydantic v2 · Pandas · NumPy · SQLite · pytest
· LangGraph 1.2（Agent 编排，用户 2026-10-26 选定；传递引入 langchain-core）
（M3 检索为**无新增依赖**的词法 TF-IDF 基线；chromadb/嵌入模型待审批后再引入。
LLM 通过可替换端口接入：默认 `none`（离线降级），测试与演示用 `scripted` 确定性替代，
真实 OpenAI 兼容端点由 `PMA_LLM_*` 环境变量启用，不引入 SDK。）

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

# 运行 Agent 场景评测（11 个确定性场景：出单/拒绝/幂等/注入/越权等，报告写入 data/processed/）
.venv\Scripts\python scripts\evaluate_agent.py


# 运行基线评测（多 seed 退化检出率 / 提前量 / 误报，报告写入 data/processed/）
.venv\Scripts\python scripts\evaluate_baseline.py

# 启动 API
.venv\Scripts\python -m uvicorn app.main:app --port 8000
# 打开 http://127.0.0.1:8000/docs

# 启动看板（另开一个终端；默认连 http://127.0.0.1:8000，可用 PMA_API_BASE_URL 覆盖）
.venv\Scripts\streamlit run app\dashboard\ui.py
# 打开 http://localhost:8501 —— 风险看板 / 设备详情与维修建议 / 工单审核 / 操作日志

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
（相似度不等于操作授权）。该端点是演示面；M4 Agent 以**库方式**调用同一检索器（不经 HTTP）。

## Agent 与工单 API（M4）

```powershell
# 一键评估：状态 → 预测 → 知识检索（+LLM 相关性复核）→ 诊断 → 备件核对 → 生成待审核工单
$body = '{"asset_id":"PUMP_001","horizon_hours":24}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/maintenance/assess `
    -ContentType "application/json; charset=utf-8" -Body $body

# 查询工单（?asset_id=&status= 过滤）
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/work-orders

# 审批（服务端校验角色白名单：X-Approver-Id / X-Approver-Role 头；body 只含决定）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/work-orders/WO-000001/approve `
    -Headers @{ "X-Approver-Id"="engineer-7"; "X-Approver-Role"="MAINTENANCE_ENGINEER" } `
    -ContentType "application/json; charset=utf-8" -Body '{"decision":"APPROVE"}'

# 提交（仅 APPROVED 状态可提交；服务端复核审批记录，重复提交幂等）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/work-orders/WO-000001/submit
```

要点：编排是 **LangGraph 有界业务状态机**（不是自由 ReAct），工具顺序由图结构决定；
LLM 只做知识相关性复核与诊断综合，无权选择工具、无权审批——`submit_work_order`
不接收任何审批参数，服务端复核 `APPROVED + approved_by` 后才执行。同一风险事件
（资产 + 数据窗口 + 维护类型）重复评估幂等复用同一工单。LLM 未配置（默认 `none`）
时显式降级：不做诊断综合、动作退化为通用现场检查并留痕说明，绝不虚构故障模式；
知识索引缺失同样显式降级。每次运行落盘 `agent_runs`（状态、工具轨迹、结果）。

## 接入真实 LLM（可选能力，OpenAI 兼容端点）

Agent 的 LLM 是可替换端口：默认 `none`（离线降级）、测试用 `scripted`（确定性替代）、
真实供应商走 **任意 OpenAI 兼容端点**（stdlib 实现，无 SDK，密钥只存环境变量）。
传输层内置有界重试（网络错误 / HTTP 429、5xx）、JSON 模式自动回退（服务器拒绝
`response_format` 时降级为普通请求）、错误信息不含密钥。

### 以 DeepSeek 为例（PowerShell，会话级环境变量）

```powershell
$env:PMA_LLM_PROVIDER = "openai_compatible"
$env:PMA_LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:PMA_LLM_MODEL    = "deepseek-chat"
$env:PMA_LLM_API_KEY  = "<你的密钥>"   # 绝不写入代码或仓库
# 可选：$env:PMA_LLM_TIMEOUT_S="60"（本地小模型建议调大） $env:PMA_LLM_MAX_RETRIES="2" $env:PMA_LLM_JSON_MODE="1"

# 1) 连通性与契约探针（只打印密钥长度，绝不回显密钥；0=就绪）
.venv\Scripts\python scripts\llm_probe.py

# 2) 用真实 LLM 重跑同一套 11 场景评测（门禁与 scripted 基线完全相同，结果如实记录）
.venv\Scripts\python scripts\evaluate_agent.py --provider openai_compatible --out data\processed\agent_evaluation_deepseek.json
```

其他端点只需换两个变量：Qwen（`https://dashscope.aliyuncs.com/compatible-mode/v1` +
`qwen-plus`）、OpenAI（`https://api.openai.com/v1` + `gpt-4o-mini`）、本地 Ollama
（`http://localhost:11434/v1` + 模型名，无需密钥）。

### 真实 LLM 下的行为语义

- LLM 职责不变：只有知识相关性复核与诊断综合；工具顺序由图决定，审批只认服务端
  记录——**真实 LLM 无法绕过任何安全门禁**（S7 注入场景在任何 provider 下都必须零副作用）；
- 端点不可用 / 返回非法 JSON → 该次评估以显式失败态留痕（`FAILED_LLM_UNAVAILABLE` /
  `FAILED_DIAGNOSIS_INVALID`），绝不静默降级为编造内容；
- 真实 LLM 输出**非字节级可复现**（temperature=0 缓解但不消除方差）：评测报告标记
  `deterministic: false` 并记录模型与传输配置；门禁与基线一致，失败场景如实呈现；
- 单元测试与 CI 保持全离线（scripted）；真实供应商评测是一次性的、显式标注的运行。



## 项目结构

```
app/
├── main.py            # FastAPI 入口（/health、/assets、POST /predictions、POST /knowledge/search、POST /maintenance/assess、/work-orders 及审批/提交）
├── config.py          # 配置与示例阈值（EXAMPLE_RULE，需设备工程师确认；含 LLM provider 与审批白名单）
├── schemas.py         # 冻结的 v1 数据契约（传感器 / 质量报告 / 风险评估 / 知识检索 / 工具与工单 / Agent 运行记录）
├── agent/
│   ├── llm.py         # 可替换 LLM 端口：none / scripted（确定性替代）/ openai_compatible（stdlib）
│   ├── tools.py       # spec §6.3 六工具：闭式契约 + ToolSpec + 轨迹审计 + 服务端信任边界
│   ├── graph.py       # LangGraph 有界状态机：状态→预测→知识→诊断→备件→工单，条件边与显式失败态
│   └── runner.py      # 运行装配与 agent_runs 落盘
├── workorders/
│   └── service.py     # 工单生命周期：幂等创建、角色白名单审批、提交前服务端复核
├── dashboard/
│   ├── backend.py     # 看板 fetch 层（无 streamlit）：§8.3 契约的 httpx 客户端 + 错误态 + 展示整形
│   └── ui.py          # Streamlit 薄壳：风险看板 / 设备详情与维修建议 / 工单审核 / 操作日志
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
│   ├── models.py      # SQLite 表结构（spec §8.2 的 8 张核心表；work_orders 幂等键唯一）
│   └── repository.py  # 数据访问（assets / sensor_readings / predictions / inventory / work_orders / agent_runs）
data/
├── raw/               # 合成原始数据 CSV + 生成 manifest（含 SHA-256）
├── knowledge/         # 演示知识库语料（14 份合成 Markdown，覆盖手册/SOP/案例/BOM）+ RAG 评测集 + Agent 场景清单
└── processed/         # 校验后数据、隔离区、质量报告、模型/检索/Agent 评测报告、知识索引
scripts/
├── seed_demo.py           # 一键演示数据入库
├── build_knowledge_index.py  # 知识索引构建 CLI
├── evaluate_baseline.py   # 基线评测：检出率 / 提前量 / 误报（spec §10.2 指标）
├── evaluate_rag.py        # 检索评测：Recall@5 / MRR / 引用 / 版本 / 拒绝（spec §7.3/§10.2）
├── evaluate_agent.py      # Agent 场景评测：出单/拒绝/幂等/注入/越权等 11 场景（--provider 可选真实 LLM）
└── llm_probe.py           # 真实 LLM 端点探针：连通性 + JSON 契约（不回显密钥）
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
| M3 维修知识 RAG | 文档入库、元数据过滤、带引用检索、评测集 | ✅ |
| M4 Agent 与工具 | 6 个工具、LangGraph 状态机、LLM 相关性复核、服务端审批、幂等 | ✅ |
| M5 前端集成 | Streamlit 看板四页、端到端联调（HTTP 契约 + 真实 uvicorn 自动化验证） | ✅ |
| M6 测试与验收 | 五层测试体系收口、§10.2 指标对照、评测汇总、影子运行试点计划 | ✅ 当前 |

## 重要声明

- 所有传感器数值均为**确定性模拟数据**，仅用于演示与测试，不代表任何真实工厂测量值。
- `config.py` 中的阈值均标记为 `EXAMPLE_RULE` 占位值，**必须经设备工程师确认后**才能用于真实设备。
- 工单提交经服务端审批校验（角色白名单 + 状态机复核，M4 已实现并有测试证明）；
  审批身份为演示级声明式请求头，真实认证属生产加固（spec §8.2）。Agent 不直接控制任何设备。
- M4 的 `scripted` LLM 是**确定性规则替代实现，不是真实大模型**：评测证明的是编排、
  契约与安全门禁的正确性；真实供应商经 `PMA_LLM_*` 环境变量接入后须重跑同套评测。
