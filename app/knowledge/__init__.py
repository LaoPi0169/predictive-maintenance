"""Maintenance-knowledge RAG package (M3, spec/Project.md section 8.1).

``ingest_docs`` turns Markdown documents into a deterministic TF-IDF index;
``retriever`` serves filtered, citation-backed search on top of that index.
"""