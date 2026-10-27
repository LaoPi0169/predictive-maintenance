# 后续路线图（M6 之后）：CMMS 对接 · 方案 B · 跨进程 Checkpoint · 容器化部署

- 日期：2026-10-27 · 状态：方案与部署文件交付（实施待排期/待真实系统）
- 定位：M1–M6 已完成 spec §四 路线图；本文给出四个已识别改进项的
  **可执行方案**，供试点（`docs/pilot-plan.md`）通过后排期。

---

## 1. 真实 CMMS 对接（M7 候选）

### 1.1 现状与目标

现状：`work_orders.submit` 将 `external_ref` 写为 `CMMS-<id>`（模拟），提交
即终点。目标：提交动作转发到真实 EAM/CMMS，并满足
`skills/agent-tool-integration` §6 的写路径四步（preview → 审批 → **幂等执行**
→ **读回校验**）。

### 1.2 架构：可替换 CmmsPort（延续 LLM 端口模式）

```
app/cmms/
├── __init__.py
├── port.py        # CmmsPort 协议 + SimulatedCmmsClient（现行为）+ NoneCmmsClient
└── rest.py        # RestCmmsClient（stdlib urllib，OpenAPI/REST，无 SDK）
```

- `CmmsPort` 协议：`create_work_order(payload: dict) -> CmmsReceipt`、
  `get_status(external_ref: str) -> CmmsStatus`；
- 选择由 `PMA_CMMS_PROVIDER = simulated | rest | none`（env）驱动，配置
  `PMA_CMMS_BASE_URL / PMA_CMMS_API_KEY / PMA_CMMS_TIMEOUT_S`——密钥只存环境
  变量，与 LLM 端口同一模式；
- `workorders/service.py::submit` 在写 `SUBMITTED` 前调用端口，失败语义见 1.5。

### 1.3 写路径四步语义（关键设计）

1. **preview**：工单草稿即 preview（M4 已实现，字段闭式校验）；
2. **approval**：服务端审批记录（M4 已实现，提交前复核）；
3. **幂等执行**：外部调用携带我方幂等键（header `Idempotency-Key: <幂等键>`
   或外部单号字段）；**超时/结果未知时：先 `get_status` 查询再决定重试**——
   绝不把"未知"当"失败"盲目重试（防止外部重复建单）；
4. **读回校验**：创建成功后立即 `get_status` 读回，确认外部状态与单号一致；
   读回失败 → 工单保持 APPROVED 并记录错误（可安全重试）。

### 1.4 字段映射（必须配置化）

真实 CMMS 字段各异，映射表放 `config/cmms_mapping.json`（随镜像分发、可
挂载覆盖）：我方字段 → 外部字段（如 `asset_id → equipment_code` 需
**资产对照表**、`priority → 枚举映射`、任务列表拼接格式）；映射缺失的
必填字段 → 显式报错回人工，不猜。

### 1.5 错误分类与状态机（不新增状态）

| 外部响应 | 行为 |
|---|---|
| 2xx + 读回一致 | SUBMITTED + external_ref=外部单号 |
| 网络错误 / 429 / 5xx | 有界重试（幂等键保护）→ 仍失败：工单保持 APPROVED，错误留痕（tool trace / 日志），可再次 submit |
| 4xx（字段/权限/重复） | 立即失败，工单保持 APPROVED，错误体透传给 UI/调用方 |

不引入 `SUBMIT_FAILED` 状态：失败即"仍是 APPROVED、可重试"，状态机保持
M4 语义，避免终态膨胀。

### 1.6 任务分解与工作量

T1 端口+模拟实现（0.5d）→ T2 RestCmmsClient + 幂等/读回（1d）→ T3 映射
配置 + 服务集成（0.5d）→ T4 mock CMMS 服务器测试（成功/超时读回/重复防护/
4xx/密钥不外泄，1d）→ T5 文档。**合计 ≈ 3 天**（不含与真实 CMMS 厂商联调）。

### 1.7 风险

外部系统字段/权限差异（最大变量）；幂等键不被外部支持（需外部单号查询
补偿）；网络分区（OT/IT）——均需试点期与厂商确认。

---

## 2. 有监督模型（方案 B，spec §5.2）

### 2.1 前提与诚实定位

方案 A（`rules-stat-v1`，已交付）无需标签；方案 B 需要**真实故障标签**
（`failure_events`）。合成数据只能证明**训练-评估-集成管线正确**，不能证明
真实预测力——试点期标签积累后重训才产生业务价值。spec §10.2：评估必须按
时间/故障事件划分（禁止随机拆分），逐故障模式统计，稀疏故障报样本量与
置信区间。

### 2.2 管线设计

```
app/prediction/train.py        # 训练入口（CLI）
data/processed/model_<ver>.joblib + model_<ver>.meta.json   # 版本化产物
```

- **数据集**：全部 accepted 读数 → `build_feature_windows`（**与在线推理同一
  特征工程函数，杜绝 train/serve skew**）→ 按时间切分：训练集 = 首段，
  测试集 = 尾段 + 每次故障事件前后窗口；标签 = 窗口终点后 H 小时内是否故障；
- **模型**：基线 `LogisticRegression` + 主力 `HistGradientBoosting`；
  **概率校准**（`CalibratedClassifierCV`, isotonic）——校准后才能回填
  `confidence`（契约已支持 0–1，此前恒 null）；
- **版本化**：`model_version="gbm-v1"`，meta 记录训练数据时间范围、特征
  版本、类别权重、评估指标、训练时间（spec §十一.2 要求）；
- **集成**：`PMA_PREDICTION_MODEL = rules | gbm`（env，默认 rules）；
  gbm 未加载/推理失败 → **降级回规则引擎并留痕**（绝不静默、绝不编造）；
  `RiskAssessment.confidence` 由 gbm 填充校准概率，`model_version` 同步；
- **评估**：与方案 A 同表对比（召回率/误报率/提前量/PR-AUC），写入
  `model_evaluation.json` 扩展段；门禁同 §10.2。

### 2.3 任务分解与工作量

T1 依赖审批（scikit-learn，依赖政策）→ T2 数据集构建（时间切分 + 标签对齐）
→ T3 训练 CLI + 校准 + 版本化产物 → T4 评估对比 → T5 predict 集成 + 降级 →
T6 测试（数据泄漏防护、降级、confidence 回填）。**合计 ≈ 4 天**。

### 2.4 风险

真实标签稀少（先做事件级小样本 + 置信区间）；类别极不平衡（class_weight /
PR-AUC 为主指标）；漂移（特征 PSI 监控 → M7+）。

---

## 3. LangGraph 跨进程 Checkpoint

### 3.1 当前架构与动机

当前：Agent 图单进程一次跑完；人工审批在**图外**（`work_orders` 表为真相源，
REST 触发）——简单、耐重启（M4 决策，已验证）。动机（演进项）：① 审批等待
建模为**图内 interrupt**，全流程单图可恢复；② 未来长流（多步确认、长时工具）
断点续跑；③ 多 worker 分布式执行。

### 3.2 方案

- 依赖：`langgraph-checkpoint-sqlite`（单机 SqliteSaver）；
  生产多副本 → `langgraph-checkpoint-postgres`；
- `build_graph(..., checkpointer=SqliteSaver.from_conn_string(
  str(config.CHECKPOINT_DB)))`，`thread_id = run_id`；
- **interrupt 审批节点**（可配置开关 `PMA_AGENT_WAIT_APPROVAL`，默认 `0`
  保持现行为）：`draft_work_order` 后 `interrupt({"work_order_id": ...})` →
  进程可退出；`approve` REST 在校验通过后以同 thread_id
  `Command(resume=...)` 恢复 → 图内 `submit` 节点完成执行；
- **与现有真相源的关系不变**：`work_orders` 表仍是业务真相，checkpoint 只是
  执行状态（COMPLETED 运行的 checkpoint 保留 N 天后清理）；
- `agent_runs` 审计语义不变。

### 3.3 任务分解与工作量

T1 依赖 + checkpointer 装配 → T2 interrupt 节点（开关默认关）→ T3 approve
端点 resume 集成 → T4 清理策略 → T5 测试（**模拟进程重启**：新进程以同
thread_id resume；审批前后两个进程完成一次运行）。**合计 ≈ 2–3 天**。

### 3.4 风险

checkpoint 格式随 langgraph 版本演化（升级需迁移/清理）；SqliteSaver 并发
写锁（单写者）；interrupt 语义与现有"图外审批"并存时的行为矩阵需测试覆盖。

---

## 4. Ubuntu Docker 容器化部署

### 4.1 交付物与验证状态

已入库文件：`Dockerfile`、`docker-compose.yml`、`.dockerignore`、
`docker/entrypoint_api.py`（启动前数据准备：按需 seed + 按需建索引）、
`tests/test_entrypoint.py`（数据准备逻辑单测）。

**验证状态（诚实声明）**：entrypoint 数据准备逻辑已本机单测验证；
镜像构建与容器运行**未在本机验证（开发机无 Docker）**——Ubuntu 上按 §4.3
首建时请先执行 §4.5 冒烟清单，任何偏差按清单记录并修正。

### 4.2 镜像与拓扑设计

- **单镜像、双服务**：同一 `pma:<ver>` 镜像，compose 中 `api`（uvicorn:8000）
  与 `dashboard`（streamlit:8501）两个服务、不同 command；
- 基础镜像 `python:3.13-slim`；非 root 用户 `appuser`；依赖层缓存
  （先 COPY requirements.txt 再 install）；
- **数据持久化**：named volume `pma-data` 挂载 `/app/data`（SQLite +
  知识索引）；entrypoint 启动时：`PMA_DEMO_SEED=1` 且库为空 → seed_demo；
  索引缺失 → 自动构建（容器重启/升级不丢数据、不丢索引）；
- **网络**：compose 内部网络，dashboard 经 `http://api:8000` 访问 API
  （`PMA_API_BASE_URL`）；仅 dashboard(8501) 与 api(8000) 对外发布（可只发
  8501 + 反代）；
- **健康检查**：api 用 Python urllib 探 `/health`（slim 无 curl）；dashboard
  探 8501 端口；`depends_on: condition: service_healthy`。

### 4.3 Ubuntu 安装 Docker（22.04/24.04，官方源）

```bash
# 1) 卸载旧版（如有）
sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
# 2) 依赖与官方 GPG/仓库
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
# 3) 安装并自启
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
# 4)（可选）免 sudo：sudo usermod -aG docker $USER && 重新登录
docker --version && docker compose version   # 验证
```

### 4.4 首次部署步骤

```bash
# 1) 获取代码
sudo apt-get install -y git
git clone <repo-url> predictive-maintenance && cd predictive-maintenance
# 2) 构建并启动（首次构建约 2–5 min：安装依赖层）
PMA_VERSION=0.5.0 docker compose up -d --build
# 3) 状态
docker compose ps                 # api healthy / dashboard running
docker compose logs -f api        # 观察种子/索引构建与 uvicorn 启动
```

首次启动 entrypoint 自动：种子演示数据（PUMP_001 + 库存主数据）→ 构建知识
索引（25 chunks）→ 起 uvicorn。要接入**真实数据**时：置 `PMA_DEMO_SEED=0`
并把已填好的 `pma.db` / 索引文件放入 volume（`docker volume inspect
pma-maintenance_pma-data` 定位路径），或挂载宿主目录替代 named volume。

### 4.5 冒烟验证清单（Ubuntu 首建必做）

```bash
curl -fsS http://localhost:8000/health          # {"status":"ok","version":"0.5.0",...}
curl -fsS -X POST http://localhost:8000/api/v1/maintenance/assess \
  -H 'Content-Type: application/json' -d '{"asset_id":"PUMP_001"}' | head -c 400
#   → COMPLETED + PENDING_APPROVAL（浏览器打开 http://localhost:8501 四页走查）
# 审批→提交（带演示身份头）后确认工单 SUBMITTED、CMMS-<id> 存在
docker compose restart api && curl -fsS http://localhost:8000/health   # 重启后数据仍在（volume 持久化）
```

### 4.6 数据持久化与备份

- 数据都在 volume `pma-data`（`/app/data`：pma.db、知识索引、评测报告）；
- 备份：`docker compose exec api python -c "import shutil,app.config as c; \
  shutil.copy(c.DB_PATH, c.DB_PATH.with_suffix('.bak'))"` 后
  `docker run --rm -v pma-maintenance_pma-data:/data -v $PWD:/backup alpine \
  tar czf /backup/pma-data-$(date +%F).tgz /data`（建议 cron 每日）；
- 恢复：停服务 → 解包覆盖 volume → 起服务。

### 4.7 升级流程

```bash
git pull
PMA_VERSION=<new> docker compose up -d --build   # 重建镜像、滚动替换容器
docker compose logs api                          # schema 为 CREATE IF NOT EXISTS，自动迁移
docker image prune -f                            # 清理悬空镜像
```

### 4.8 安全加固清单（Ubuntu 生产化）

- `sudo ufw allow 8501/tcp && sudo ufw enable`（仅暴露看板；API 走内网/反代）；
- TLS：前置 Caddy/nginx 反代 + 证书，公网不直连 8501/8000；
- 镜像内非 root 已启用；密钥（`PMA_LLM_API_KEY` 等）经 compose
  `env_file`/docker secrets 注入，**不写入镜像与仓库**；
- `restart: unless-stopped` + `docker.service` 开机自启 = 服务器重启自动恢复；
- 资源限制：compose `deploy.resources.limits`（建议 api 1–2 GB）；
- 定期 `docker compose pull` / 基础镜像更新 + 扫描（`docker scout`/trivy）。

---

## 5. 建议排期

| 项 | 前置条件 | 工作量 | 建议顺序 |
|---|---|---|---|
| Docker 容器化（Ubuntu 首建验证） | 无 | 0.5–1 天（含冒烟修正） | **先做**（部署底座） |
| 真实 CMMS 对接 | 试点 + 厂商字段/测试环境 | ≈ 3 天 + 联调 | 试点通过后 |
| 跨进程 Checkpoint | 无（纯仓库内） | ≈ 2–3 天 | 随需（引入图内等待时） |
| 方案 B 有监督模型 | 真实故障标签 + sklearn 审批 | ≈ 4 天 | 标签积累后 |

