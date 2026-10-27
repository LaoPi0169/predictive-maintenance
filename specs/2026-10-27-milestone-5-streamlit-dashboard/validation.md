# M5 验证计划（validation.md）

## 1. 测试与验证项

### 单元 / 集成测试
1. `GET /api/v1/agent-runs`：空库 []、完整 AgentRunResult 回读、过滤与 limit；
2. fetch 层契约（ASGITransport 离线 HTTP 栈）：各端点成功形状、
   404/403/409/503 错误态透传、后端不可达 BACKEND_UNREACHABLE、
   审批头注入；
3. 端到端业务流（T4）：预测 → Agent 出单 → 审批 → 提交 → 审计；
4. UI 模块可导入冒烟（streamlit 顶层无副作用）。

### 人工验收清单（看板演示）
- [ ] 后端未启动时健康灯红、页面无假数据；
- [ ] 风险看板：种子设备出现且风险徽章颜色正确；
- [ ] 设备详情：预测评估 + Agent 评估全程可见（证据/候选/引用/备件/横幅）；
- [ ] 工单审核：approve→submit 全链路；403/409 显示服务端原因；
- [ ] 操作日志：agent_runs 工具轨迹可展开，predictions 含失败态留痕。

## 2. 命令

```bash
python -m pytest
# 演示（两个终端）
.venv\Scripts\python -m uvicorn app.main:app --port 8000
.venv\Scripts\streamlit run app\dashboard\ui.py
```

## 3. Definition of Done

- [ ] 上述全部测试通过且数字为真实运行结果；
- [ ] 端到端业务流经 fetch 层自动化验证；
- [ ] 后端不可达/错误态在看板中有显式呈现（不渲染假数据）；
- [ ] README / CHANGELOG 更新；演示级身份、单用户假设如实声明。

## 4. 已知局限（如实声明）

- 审批身份仍是演示级声明式头（无登录会话）；
- 单用户演示假设（Streamlit rerun 模型）；无自动刷新/实时推送；
- UI 渲染本身不做自动化（薄壳设计 + 人工验收清单替代）。

---

## 实际验证结果（2026-10-27 实测）

### 测试汇总
- `.venv\Scripts\python -m pytest` → **240 passed**：M1–M4 回归 222 项全部保持
  通过，M5 新增 18 项（/agent-runs 端点 4、fetch 层契约 10、端到端业务流 2、
  UI 导入冒烟与展示整形并入其中）。

### 端到端联调（自动化，真实 uvicorn @ 临时端口）
- **高风险全链路**：list_assets → run_prediction（EMERGENCY）→
  run_agent_assessment（COMPLETED、PENDING_APPROVAL、知识引用非空）→
  list_work_orders(PENDING_APPROVAL)=1 → approve（APPROVED，approved_by 落库）→
  submit（SUBMITTED，external_ref=CMMS-WO-…）→ list_agent_runs 工具轨迹
  五步完整、predictions 审计 OK、SUBMITTED 过滤=1；
- **健康资产分支**：NORMAL → Agent 评估不出单、requires_human_approval=False、
  工单表空；
- **错误态**：未知资产 404（ASSET_NOT_FOUND 透传）、数据不足 503（审计态返回
  而非崩溃）、未审批提交 409、越权角色 403（零副作用）、后端不可达
  BACKEND_UNREACHABLE（含启动提示）。

### 人工验收清单
- [x] fetch 层与 UI 分离（backend.py 无 streamlit 导入；ui.py 导入冒烟通过）；
- [x] 后端不可达 → BACKEND_UNREACHABLE + base_url 提示（自动化覆盖）；
- [x] 四页渲染逻辑走查（人工，浏览器演示）——数据全部来自 §8.3 契约。

### 已知局限
- 审批身份仍为演示级声明式头（无登录会话）；单用户演示假设；
- 无自动刷新/实时推送；UI 渲染本身无浏览器级自动化（薄壳 + 人工验收清单）。

### DoD 核对
- [x] 全部测试通过（240 passed，真实运行结果）
- [x] 端到端业务流经 fetch 层自动化验证（真实 HTTP 栈）
- [x] 错误态显式呈现、不渲染假数据（错误透传测试证明）
- [x] README / CHANGELOG / requirements.txt 更新；局限如实声明

