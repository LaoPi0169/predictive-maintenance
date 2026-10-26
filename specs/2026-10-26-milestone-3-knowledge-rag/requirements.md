# M3 需求规格：维修知识 RAG（Knowledge RAG）

- 日期：2026-10-26
- 来源：`spec/Project.md`（§7.1–7.3 知识库与检索、§8.1 目录、§10.1 第三层 RAG
  测试、§10.2 RAG 验收指标）
- 工作流：`skills/rag-feature-spec`
- 上游：M1 数据地基 + M2 预测与风险（已交付：四级风险与证据、预测 API、基线评测）

## 1. 业务问题与用户

M2 回答"设备有多大风险"；M3 按 spec §七回答"应该检查什么、为什么检查"——
为风险结论提供维修知识依据（spec §1.2 第 3 步：查询历史故障、维修记录、
设备手册和 SOP）。检索结果将作为 M4 Agent 只读工具 `search_knowledge` 的
数据面，并支撑验收指标"关键维修建议有有效依据 100%"（spec §10.2）。

主要用户：维修/设备工程师（直接检索演示）；M4 Agent（编程调用方，需要
结构化、可回溯的引用）。

## 2. 范围

### In scope（本里程碑）

1. **演示知识库语料**（`data/knowledge/`，提交入库的合成 Markdown，覆盖
   spec §7.1 的四类资料）：
   - 设备技术手册（P-103 离心泵结构与运行范围、电机电流诊断参考）；
   - 维修 SOP（振动检查、轴承温度异常、润滑更换、轴承更换、安全隔离
     LOTO；另含 1 份被替代的旧版 v1.0 与 1 份未批准草稿用于版本过滤）；
   - 历史故障与维修案例（轴承磨损、机械密封泄漏、联轴器不对中）；
   - 设备 BOM 与备件适配规范（轴承 6208-2RS 与替代件审批规则）。
   每份文档携带 spec §7.2 要求的元数据：`document_id`、`asset_model`、
   `component`、`document_type`、`document_version`、`effective_date`、
   `approval_status`、（分块级）`section_title`、`source_location`。
2. **文档入库**（`app/knowledge/ingest_docs.py`，spec §8.1 命名）：
   元数据闭式校验（未知字段拒绝、`document_id` 唯一）→ 确定性结构分块
   （按标题/段落，块上限与合并下限可配；spec §7.2 禁止把 SOP 拆成失去
   上下文的零碎句子）→ TF-IDF 向量化 → 索引落盘
   `data/processed/knowledge_index.{npz,json}`（含语料内容哈希、构建参数、
   chunk 系谱）。重建幂等、确定性（除构建时间戳）。
3. **带元数据过滤的检索**（`app/knowledge/retriever.py`）：过滤
   （doc_type / asset_model / component；默认仅 APPROVED 且未被替代）→
   余弦相似度 → top_k → 最低分阈值；低于阈值时 `can_answer=false` 并显式
   给出拒绝原因（spec §10.1 第三层"资料不足时拒绝猜测"）。引用（citation）
   携带 `chunk_id/doc_id/版本/章节/source_location`，可回溯原文。
4. **检索 API**（`app/main.py`）：`POST /api/v1/knowledge/search`
   （§8.3 之外的新增端点，理由见 §4）。
5. **检索评测**（`scripts/evaluate_rag.py` + `data/knowledge/eval_set.json`，
   spec §7.3/§10.2）：30 条评测问题（答案型 / 过滤型 / 拒绝型），指标
   Recall@5（目标 ≥0.9，spec §10.2 示例值）、MRR、版本正确率（不返回
   SUPERSEDED/DRAFT）、引用有效率（引用可解析且指向现行已批准文档）、
   拒绝正确率；输出 `data/processed/rag_evaluation.json`。
6. **测试**：spec §10.1 第三层（检索正确性、引用有据、过期 SOP 被过滤、
   资料不足拒绝猜测）。

### Out of scope（后续里程碑）

- LLM 生成诊断答案与 Agent 编排（M4）；embedding 语义模型 / chromadb
  （见 §4 决策）；知识库写 API 与文档权限 ACL（M4+）；库存兼容性校验
  （M4 备件工具）；PDF/Word 解析（语料即 Markdown）；评测集规模扩至
  50+ 与真实工程师审核（真实语料接入时）。

## 3. 新增数据形状（契约摘要）

- `KnowledgeChunk`（闭式）：spec §7.2 全部字段 + `chunk_id =
  {document_id}#c{序号}` + `superseded_by`。
- `Citation`（闭式）：`chunk_id / doc_id / doc_version / section_title /
  source_location / score`——M4 引用校验（"引用是否真的支持建议"）的
  最小单元。
- `KnowledgeHit`（闭式）：`citation + text`（命中块原文，供证据展示）。
- `RetrievalResult`（闭式）：`query / top_k / 实际应用的过滤条件 /
  can_answer / insufficient_reason / retrieval_version /
  results: list[KnowledgeHit]`。

## 4. 决策与理由

| 决策 | 理由 |
|---|---|
| 词法 TF-IDF + 余弦（numpy），不引入 chromadb/嵌入模型 | 语料约 50 块，精确穷举即可、离线、字节级可复现；chromadb 默认嵌入首次使用需联网下载模型，破坏 M1/M2 建立的离线确定性；依赖政策要求按里程碑审批。诚实定位：这是**词法检索基线**，不是语义检索；契约带 `retrieval_version`，后续换真嵌入时调用方不变 |
| 知识源与索引用文件，不加新表 | spec §8.2 未定义知识表（M1 隔离区同款先例）；demo 规模内存过滤足够；生产化时迁库/向量库，契约不变 |
| 默认仅检索 APPROVED 且现行版本 | spec §7.2："向量相似度高，不代表内容具有操作授权"；SUPERSEDED/DRAFT 仅显式请求时可见 |
| 分块按文档结构而非定长硬切 | spec §7.2：不要把安全操作步骤拆成失去上下文的零碎句子 |
| 新增 `/api/v1/knowledge/search`（§8.3 表之外） | 检索是 M4 工具的数据面，API 化便于演示与契约测试（/health 已有先例）；端点 docstring 声明为演示面 |
| 评测集 30 条（下限）且与语料同源 | spec §7.3 允许 30–50 条启动开发；诚实声明：由开发者编写于同一合成语料，只证明**管线正确性**（索引、过滤、排序、拒绝），不证明生产检索精度 |
| 构建期 fail-fast 校验元数据 | 坏元数据混入索引会污染全部下游引用；显式报错优于静默降级 |
| 索引带语料哈希，不匹配即拒绝服务 | 防止"悄悄用旧知识回答"——与 M2 失败态留痕同一哲学 |

## 5. 用户角色、权限与数据边界

- 只读检索，无外部副作用，无新增权限面（ACL 延后，spec §8.2 注记 M4+）；
- 审批状态只来自文档元数据；检索分数不得改变授权语义（guardrail：
  检索/模型输出不能决定 approval、citation 有效性或业务状态）；
- 全部语料为合成演示内容并在 front matter 显式标注来源，不对应真实
  设备手册；不放入任何敏感内容。

## 6. 预期失败状态与行为

| 失败状态 | 行为 |
|---|---|
| 索引未构建 | API 503 `INDEX_MISSING`，提示运行构建脚本 |
| 语料哈希 ≠ 索引哈希（索引过期） | API 503 `INDEX_STALE`——绝不安静地用旧知识回答 |
| 查询无可靠证据（低于阈值/过滤后为空） | 200 + `can_answer=false` + 原因；不编造引用 |
| 请求体非法（空 query、top_k 越界、未知 doc_type） | 422（FastAPI 校验） |
| 文档元数据缺失/未知字段/document_id 重复 | 构建期报错，拒绝生成索引 |
| 语料目录不存在 | 构建期报错 |

## 7. 假设与未决问题

- **假设**：语料、评测集、分块与检索阈值均为演示值（EXAMPLE_RULE），
  接入真实手册与工程师审核的问题集后必须重做；
- **假设**：TF-IDF 词法匹配对同义改写、跨语言查询弱——已知局限；
- **假设**：引用有效率的自动检查是**结构校验**（引用可解析、指向现行
  已批准文档）；"引用是否支持建议"的语义审核属 spec 第五层人工验收；
- **未决**：chromadb / 嵌入模型的引入时机与审批（M4+）；中文分词策略
  （当前 CJK 字符二元组 + ASCII 词，真实中文语料接入后重评）。