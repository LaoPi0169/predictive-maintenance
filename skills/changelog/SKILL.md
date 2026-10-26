---
name: changelog
description: Updates the project CHANGELOG.md from Git history and reviewed feature changes. Use before merging a RAG/Agent feature or when asked to record project changes.
---

# Changelog Skill

## Workflow

1. Run the changelog script from the project root, where `.git/` exists:

   ```bash
   python3 <skill-dir>/scripts/changelog.py
   ```

2. Review the actual Git diff and generated entries. Commit subjects may be vague or include internal implementation details; edit wording so the changelog describes user-visible or engineering-relevant changes accurately.
3. Confirm that tests/evaluation evidence exists before wording a change as validated. A changelog records changes; it does not prove quality or production readiness.
4. Commit `CHANGELOG.md` with the feature branch or merge commit according to the repository convention.

## Format

```markdown
# Changelog

## YYYY-MM-DD

- Added source-version-aware citations to RAG responses.
- Added regression cases for stale and conflicting documents.
- Fixed ACL filtering before retrieval candidate generation.
```

- One `# Changelog` title.
- Date headings use `## YYYY-MM-DD`, newest first.
- Use concise bullets grouped by date.
- Distinguish Added / Changed / Fixed / Security / Evaluation where it improves clarity.
- Do not include credentials, sensitive customer names, raw incident payloads or confidential document text.
- Do not claim measured accuracy, ROI, SLO or adoption unless the supporting evidence exists and the metric was actually measured.

## RAG / Agent-specific notes

When relevant, mention versioned behaviour changes such as corpus/ACL handling, retrieval strategy, citation validation, refusal policy, tool schema, approval flow, model/prompt configuration and evaluation cases. Link to internal reports or manifests using repository conventions; do not paste sensitive data into the changelog.

The script is intended to maintain date-based entries from Git commit history. It is not a semantic release manager, test runner, evaluation harness or deployment record. Keep evaluation reports and release manifests as separate artifacts.
