# M3 实施计划（plan.md）

任务按依赖排序，每组可独立测试。实现顺序 = 测试顺序 = 验收顺序。

## T1. 配置与契约扩展

- `app/config.py`：`KNOWLEDGE_DIR`（语料目录）、索引文件路径
  （`knowledge_index.npz/json`）、`CHUNK_MAX_CHARS` / `CHUNK_MIN_CHARS`、
  `RETRIEVAL_TOP_K=5`、`RETRIEVAL_MIN_SCORE`（评测校准后定值，
  EXAMPLE_RULE 注释）、`RETRIEVAL_VERSION="tfidf-cosine-v1"`。
- `app/schemas.py`：`DocType` / `ApprovalStatus` 枚举，
  `KnowledgeChunk` / `Citation` / `KnowledgeHit` / `RetrievalResult`
  （闭式 schema）。
- 测试：`tests/test_schemas.py` 追加（未知字段拒绝、非法枚举、score 边界）。

## T2. 语料与文档入库

- `data/knowledge/*.md`：14 份合成文档（front matter + 正文；含
  SUPERSEDED v1.0 与 DRAFT 草稿各 1 份）+ `data/knowledge/eval_set.json`
  （30 条评测问题）。
- `app/knowledge/__init__.py`、`app/knowledge/ingest_docs.py`：
  - `load_documents(dir)`：解析 front matter，元数据闭式校验、doc_id 唯一；
  - `chunk_document(doc)`：按标题/段落结构分块（确定性、块上限/合并下限）；
  - `build_index(knowledge_dir, out_dir)`：分块 → TF-IDF（L2 归一）→
    语料 sha256 → 落盘 `knowledge_index.{npz,json}`；
  - `corpus_digest(dir)` / 索引过期判定。
- `scripts/build_knowledge_index.py`：CLI 包装。
- 测试：`tests/test_knowledge_ingest.py`（两次构建向量逐元素相等、
  未知 front-matter 字段报错、doc_id 重复报错、SUPERSEDED/DRAFT 元数据
  正确、语料变更 → 过期判定成立）。

## T3. 检索器

- `app/knowledge/retriever.py`：
  - `load(index_dir)`：文件缺失 → `KnowledgeIndexMissingError`；
    语料哈希不匹配 → `KnowledgeIndexStaleError`；
  - `search(...)`：元数据过滤 → 余弦 → top_k → 阈值 → `can_answer`；
  - 过滤语义：`asset_model` 精确匹配或 `"*"` 通用文档；默认仅 APPROVED
    现行版本；`include_unapproved` / `include_superseded` 显式放宽。
- 测试：`tests/test_retriever.py`（型号/类型/组件过滤、默认不返回
  SUPERSEDED/DRAFT、显式请求可见、乱码查询 `can_answer=false` 且无引用、
  引用字段可回溯、分数降序）。

## T4. API

- `app/main.py`：`POST /api/v1/knowledge/search`——200（含
  `can_answer=false` 的拒绝响应）、422、503 `INDEX_MISSING` /
  `INDEX_STALE`（结构化错误体，不返回编造结果）。
- 测试：`tests/test_knowledge_api.py`（tmp 索引目录注入；四态断言；
  索引过期 503）。

## T5. 检索评测

- `scripts/evaluate_rag.py`：构建索引 → 逐查询检索（默认过滤）→
  recall@5 / MRR / 版本正确率 / 引用有效率 / 拒绝正确率 →
  `data/processed/rag_evaluation.json`（含逐题命中明细与 miss 列表）。
- 测试：`tests/test_rag_evaluation.py`（真实语料上断言 recall@5 ≥ 0.9、
  版本正确率 = 1.0、引用有效率 = 1.0、拒绝题正确率 = 1.0、评测集期望
  doc 均存在于语料）。

## T6. 收尾

- 全量 `python -m pytest`，记录真实结果；
- 运行 `scripts/build_knowledge_index.py` 与 `scripts/evaluate_rag.py`，
  真实指标写入 validation.md；
- README（路线图 M3 ✅、构建/评测命令、API 示例、目录树）、
  requirements.txt 注释更新（M3 未新增依赖）、CHANGELOG；
- git commit；已知局限如实记录。

## 交付文件清单

```
app/knowledge/{__init__,ingest_docs,retriever}.py
app/{schemas,config,main}.py（扩展）
data/knowledge/*.md、data/knowledge/eval_set.json
scripts/{build_knowledge_index,evaluate_rag}.py
tests/{test_knowledge_ingest,test_retriever,test_knowledge_api,test_rag_evaluation}.py
tests/test_schemas.py（扩展）
specs/2026-10-26-milestone-3-knowledge-rag/
```