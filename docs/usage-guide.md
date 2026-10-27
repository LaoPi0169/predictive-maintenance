# Agent 使用与运行指南（当前版本 v0.5.0，M1–M6 全部交付）

本指南回答三个问题：**在哪一步做什么操作**、**每一步对应什么业务场景**、
**整体流程是怎样的**。所有命令均在本仓库实际验证过。

---

## 一、整体流程（业务全景）

```
【业务场景：设备健康管理的日常闭环】

 ①数据接入与质检      ②风险预测            ③诊断与建议           ④人工审批            ⑤执行与审计
 ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌──────────────┐   ┌──────────────┐
 │ 传感器数据    │ → │ 规则+统计     │ → │ 知识检索(RAG)+    │ → │ 维修主管/     │ → │ 模拟CMMS     │
 │ CSV/模拟流   │   │ 风险引擎      │   │ LLM诊断综合       │   │ 设备工程师    │   │ 工单执行     │
 │ 质量门禁隔离  │   │ 四级风险+证据 │   │ 备件核对+工单草稿  │   │ 白名单+状态机 │   │ 操作日志留痕  │
 └──────────────┘   └──────────────┘   └──────────────────┘   └──────────────┘   └──────────────┘
   M1 数据地基        M2 预测与风险       M3 知识RAG + M4 Agent   人工在环(HITL)      M5 看板呈现
```

一句话流程：**传感器数据经质量门禁入库 → 风险引擎给出四级风险与证据 →
风险达到可操作级别（HIGH/EMERGENCY）时 Agent 检索已批准的维修知识并综合诊断 →
核对备件主数据 → 生成"待审批"工单草稿（幂等）→ 人工审批 → 提交（模拟）CMMS →
全过程留痕可审计**。

三条铁律（贯穿所有步骤）：
1. 坏数据/证据不足 → 显式报"无法评估"，绝不伪装成"低风险"；
2. 知识不足/语义域外 → 拒绝并说明，绝不编造引用与建议；
3. 工单提交只认**服务端审批记录**，模型或界面上的任何"已批准"声明都无效。

---

## 二、阶段 0：一次性准备（IT/数据场景）

在项目根目录、Windows PowerShell 下：

| 步骤 | 命令 | 做什么 | 业务场景 |
|---|---|---|---|
| 0.1 建环境 | `python -m venv .venv`<br>`.venv\Scripts\python -m pip install -r requirements.txt` | 安装全部依赖（FastAPI/LangGraph/Streamlit 等） | 开发/演示环境就绪 |
| 0.2 生成演示数据并入库 | `.venv\Scripts\python scripts\seed_demo.py` | 生成 3 天（4320 分钟）确定性"轴承退化"传感数据 → 过第一层质量门禁 → 注册设备 **PUMP_001**（型号 P-103）→ 存入 `data/pma.db` | **新设备接入系统**：历史数据导入 + 质量验收（坏数据会被隔离，不会混入） |
| 0.3 构建知识索引 | `.venv\Scripts\python scripts\build_knowledge_index.py` | 把 `data/knowledge/` 的 14 份手册/SOP/案例/BOM 分块并向量化 | **维修知识库上线**：只有已批准、现行版本的文档可被检索 |
| 0.4（可选）重置演示 | 删除 `data\pma.db` 后重跑 0.2/0.3 | 数据完全可复现（固定 seed） | 演示前回到干净状态 |

验证 0.2/0.3 成功的标志：0.2 打印 `accepted=4320 quarantined=0`；0.3 打印
chunk 数（25）与语料哈希。

## 三、阶段 1：启动服务（两个终端）

| 终端 | 命令 | 说明 |
|---|---|---|
| 终端 1（后端 API） | `.venv\Scripts\python -m uvicorn app.main:app --port 8000` | FastAPI 服务，Swagger 文档在 http://127.0.0.1:8000/docs |
| 终端 2（看板） | `.venv\Scripts\streamlit run app\dashboard\ui.py` | 看板在 http://localhost:8501 ；后端地址默认 `http://127.0.0.1:8000`，可用环境变量 `PMA_API_BASE_URL` 覆盖 |

看板侧边栏出现绿色"后端在线 · v0.5.0"即联通；红色则先检查终端 1。

---

## 四、逐步操作（看板路径，推荐演示动线）

### 步骤 1｜风险看板 ——「现在哪些设备有危险？」
**业务场景**：值班工程师晨检/巡检的总览。

操作：侧边栏选「风险看板」。
- 看到设备 PUMP_001 的卡片：最新风险徽章（🟢/🟡/🟠/🔴）、风险分（0–10，**不是
  故障概率**，confidence 恒为 null）、数据质量、评估时间；
- 顶部是风险分布统计。

> 种子数据是退化场景，首次进入通常已是 🔴 紧急（EMERGENCY，分≈7.0）。
> 页面上没有数据时先完成阶段 0。

### 步骤 2｜设备详情与维修建议——「为什么危险？该做什么？依据是什么？」
**业务场景**：设备工程师对高风险设备的深入诊断（Agent 的核心价值步骤）。

操作：侧边栏选「设备详情与维修建议」→ 选设备 PUMP_001 →

2a. 点 **「运行预测评估」**（调 `POST /api/v1/predictions`）
- 展示：风险等级/分数、**逐指标证据**（振动/温度/电流的 z 分数或超限说明，含
  阈值来源）、不确定性说明；
- 业务含义：**可解释的风险结论**——每个数字都能追溯到"参考基线"或
  "EXAMPLE_RULE 报警限值"。

2b. 点 **「运行 Agent 维护评估」**（调 `POST /api/v1/maintenance/assess`）
- 后端自动执行五步（LangGraph 有界状态机，工具顺序固定，LLM 无权自由选工具）：
  1. `get_asset_status` 设备状态与数据质量；
  2. `get_prediction` 获取已验证的预测（不自行猜风险）；
  3. `search_maintenance_kb` 检索已批准知识 + **LLM 相关性复核**（只降级不升级）；
  4. `diagnose` 严格基于证据与知识块综合诊断（置信度恒为 null）；
  5. `check_spare_parts` 备件主数据核对（✅已确认/⚠️待确认/⛔不可用）→
     `create_work_order_draft` 生成**待审批**工单（幂等键）；
- 页面展示：故障模式候选、**带 chunk 引用的建议动作**、备件需求三态、
  「待人工审批」横幅、不确定性说明、可展开的工具调用轨迹；
- 业务含义：**从"有风险"到"可执行的维修方案"**，每条建议都有出处，人工复核
  所需信息一次给全。

> 页面下方的知识库检索框可单独提问（如"轴承温度高怎么处理"），体验引用与
> "无依据即拒绝"。

### 步骤 3｜工单审核——「人来决定是否执行」
**业务场景**：维修主管/设备工程师的人工在环审批（HITL），Agent 无权绕过。

操作：侧边栏选「工单审核」→ 先在侧边栏填**审批身份**（角色下拉：
MAINTENANCE_ENGINEER / OPERATIONS_MANAGER；审批人 ID）→ 状态过滤选
PENDING_APPROVAL → 选中工单查看内容（问题描述/建议任务/备件）→
选审批决定 APPROVE/REJECT → 点 **「应用审批决定」** → 点 **「提交到 CMMS」**。

- 服务端校验三道门：角色白名单（403）、工单状态机（409）、审批记录存在性
  （409 WORK_ORDER_NOT_APPROVED）——界面上任何"已批准"都不算数；
- 提交成功显示 `external_ref=CMMS-WO-000001`（模拟 CMMS 引用；真实 CMMS
  对接属试点后生产化）。

### 步骤 4｜操作日志——「Agent 到底做了什么？」
**业务场景**：审计与复盘（出问题时可追溯）。

操作：侧边栏选「操作日志」。
- **Agent 运行**：每次运行的状态/LLM 供应商/失败原因，展开可见**逐工具轨迹**
  （哪个工具、耗时、错误码）；
- **预测审计**：含"数据不足"等失败态留痕（拒绝评估这件事本身也被记录）；
- **工单记录**：幂等键、审批人、外部引用。

### 幂等行为（重要业务保证）
对**同一风险事件**（同一设备 + 同一数据窗口 + 同一维护类型）再次运行
Agent 评估 → **不会产生第二张工单**：返回既有工单并在不确定性说明中标注
"幂等复用"。因此演示时可以反复点击"运行 Agent 维护评估"。

---

## 五、逐步操作（API 路径，集成/自动化视角）

适合展示"看板只是契约的消费者，系统集成方可以直接调 API"。PowerShell：

```powershell
# 1) 预测评估（值班巡检）
$body = '{"asset_id":"PUMP_001","horizon_hours":24}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/predictions `
    -ContentType "application/json" -Body $body

# 2) Agent 评估（诊断闭环 → 生成待审批工单）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/maintenance/assess `
    -ContentType "application/json" -Body $body

# 3) 查询待审批工单
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/work-orders?status=PENDING_APPROVAL"

# 4) 审批（服务端校验角色白名单）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/work-orders/WO-000001/approve `
    -Headers @{ "X-Approver-Id"="engineer-1"; "X-Approver-Role"="MAINTENANCE_ENGINEER" } `
    -ContentType "application/json; charset=utf-8" -Body '{"decision":"APPROVE"}'

# 5) 提交（模拟 CMMS；幂等）
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/work-orders/WO-000001/submit

# 6) 审计
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/agent-runs?asset_id=PUMP_001"
```

手工创建草稿（非 Agent 路径）：`POST /api/v1/work-orders`，body 为
`WorkOrderDraftInput`（asset_id/maintenance_type/problem_description/
recommended_tasks/risk_event_ref 必填；`approved` 之类字段会被闭式契约 422 拒绝）。

---

## 六、可选：接入真实 LLM（增强诊断与相关性复核）

默认 `PMA_LLM_PROVIDER=none`：Agent 完整可跑，但跳过 LLM 诊断综合（动作为
通用现场检查并留痕说明）。接入 DeepSeek 等 OpenAI 兼容端点后，诊断综合与
相关性复核由真实 LLM 承担（安全门禁不变）：

```powershell
$env:PMA_LLM_PROVIDER = "openai_compatible"
$env:PMA_LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:PMA_LLM_MODEL    = "deepseek-chat"
$env:PMA_LLM_API_KEY  = "<你的密钥>"      # 只存环境变量，不入库
.venv\Scripts\python scripts\llm_probe.py          # 连通性+契约探针（0=就绪）
```

之后正常使用（看板/API 均生效）。注意：端点不可用或返回非法 JSON → 该次
评估以显式失败态留痕（绝不静默编造）；真实 LLM 输出非字节级可复现。

---

## 七、质量验证与验收（评测套件，M6）

| 命令 | 验证什么 | 耗时 |
|---|---|---|
| `.venv\Scripts\python -m pytest` | 247 项全离线测试（五层体系） | ~35 s |
| `.venv\Scripts\python scripts\evaluate_baseline.py` | 模型层：5 seed 退化回放（召回/提前量/误报） | 数分钟 |
| `.venv\Scripts\python scripts\evaluate_rag.py` | RAG 层：Recall@5/引用/版本/拒绝 | ~10 s |
| `.venv\Scripts\python scripts\evaluate_agent.py` | Agent 层：11 场景（出单/拒绝/幂等/注入/越权） | ~1 min |
| `.venv\Scripts\python scripts\collect_metrics.py` | §10.2 验收指标对照汇总 | <1 s |

报告位置：`data/processed/`（rag/agent/evaluation_summary 等，gitignored）。
五层体系与指标对照的人读版：`docs/test-report.md`；试点计划：`docs/pilot-plan.md`。

---

## 八、每一步 ↔ 业务场景 ↔ 后端行为对照表

| 步骤 | 操作（看板/API） | 业务场景 | 后端发生什么 |
|---|---|---|---|
| 准备 | seed_demo / build_knowledge_index | 设备接入、知识库上线 | 质量门禁隔离坏数据；语料分块向量化 |
| 1 | 风险看板 | 值班巡检总览 | 读 assets + predictions 审计 |
| 2a | 运行预测评估 | 可解释风险结论 | 特征窗口 → 规则引擎 → 结果留痕 |
| 2b | 运行 Agent 维护评估 | 诊断闭环 → 待审工单 | 六工具链 + LLM 两职责 + 幂等建单 + agent_runs 留痕 |
| 3a | 应用审批决定 | 人工在环 | 白名单+状态机校验 → APPROVED/REJECTED |
| 3b | 提交到 CMMS | 授权执行 | 复核审批记录 → SUBMITTED + CMMS-<id> |
| 4 | 操作日志 | 审计复盘 | 读 agent_runs / predictions / work_orders |
| 评测 | evaluate_* | 质量验收（M6） | 确定性回放 + 门禁 + §10.2 汇总 |

## 九、常见错误与业务含义

| 现象（HTTP/页面） | 业务含义 | 处理 |
|---|---|---|
| 503 `CANNOT_ASSESS` / INSUFFICIENT_DATA | 证据不足，**无法可靠评估 ≠ 低风险** | 补充数据（≥120 分钟读数、≥6 个可用窗口） |
| 404 `ASSET_NOT_FOUND` | 设备未注册 | 先 seed_demo / 检查 asset_id |
| 503 `INDEX_MISSING` / `INDEX_STALE` | 知识库未建或语料已改 | 重跑 build_knowledge_index |
| 403 `FORBIDDEN` | 审批角色不在白名单 | 换白名单内角色（服务端仍兜底） |
| 409 `WORK_ORDER_NOT_APPROVED` / `INVALID_TRANSITION` | 未审批即提交 / 终态再操作 | 先走审批；REJECTED 为终态 |
| Agent 显示"通用现场检查"动作 | LLM 未配置（none）或知识证据不足的**显式降级** | 属正常诚实行为；可接入真实 LLM |
| 看板健康灯红 | 后端未启动 | 启动终端 1 |
