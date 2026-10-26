---
name: rag-feature-spec
description: Turns the next RAG or Agent roadmap item into a scoped, testable feature specification before coding. Use when asked to start a RAG feature, implement the next roadmap phase, or prepare an AI application change.
---

# RAG / Agent Feature Spec

## Purpose

Create a small, independently shippable feature spec that follows `rag-roadmap.md`. Do not start implementation until scope, decisions, and context are sufficiently clear. If the user explicitly asks you to proceed with reasonable defaults, record those assumptions rather than blocking unnecessarily.

## Workflow

### 1. Inspect the project first

Read, when present:
- `rag-roadmap.md` or `specs/roadmap.md`;
- `README.md` and `specs/mission.md`;
- `specs/tech-stack.md` or existing dependency/configuration files;
- relevant API/data/tool contracts;
- related tests and the latest `CHANGELOG.md` entries.

Identify the first incomplete roadmap item and summarize current implementation, constraints, and dependencies. Do not assume a feature is missing just because its documentation is incomplete; inspect code and tests.

### 2. Clarify the feature before writing files

Ask concise questions covering these three dimensions:

1. **Scope** — user, use case, input/output, included/excluded behaviour, data sources.
2. **Decisions** — retrieval strategy, model/provider, storage, API/UI, permission model, error handling.
3. **Context** — deadline, deployment constraints, data sensitivity, existing conventions, open questions.

If information is already in project files or the user has provided it, do not ask again. Do not create files until blocking decisions are answered or explicitly recorded as assumptions.

### 3. Create a dated spec directory

Use `specs/YYYY-MM-DD-<feature-name>/` and create:

#### `requirements.md`

Include:
- business problem and user;
- in-scope / out-of-scope;
- request/response or data shape;
- decisions and rationale;
- user roles, ACL and data boundary;
- expected failure states and refusal/escalation behaviour;
- assumptions and unresolved questions.

#### `plan.md`

Break implementation into ordered, independently testable task groups, such as:
1. contracts and schemas;
2. data/ingestion or retrieval changes;
3. service/API/agent changes;
4. UI or integration;
5. tests and evaluation;
6. docs, changelog and release evidence.

Name expected files/modules only after inspecting the actual repository. Avoid speculative architecture and unnecessary dependencies.

#### `validation.md`

Define:
- unit, integration and regression tests;
- success and failure-path assertions;
- authorization and ACL cases;
- RAG-specific evaluation cases where relevant;
- citation validity and refusal behaviour;
- tool timeout/error/idempotency tests where relevant;
- commands to run;
- Definition of Done and known limitations.

### 4. RAG-specific requirements

For a corpus or retrieval feature, specify source/version/owner/ACL metadata, chunk lineage, deterministic ingestion, duplicate/quarantine behaviour, evaluation-set split, metrics and failure categories.

For a generation feature, specify the answer/refuse/escalate contract, evidence requirements, citation validator, prompt/version binding, and behaviour when evidence is missing, stale, conflicting or unauthorized.

For an Agent/tool feature, specify tool effect (`read_only` or write), input/output schema, server-side authorization, timeout, retry semantics, audit events and a negative list of prohibited actions. For writes, require preview → structured approval → idempotent execution → independent verification.

### 5. Implementation guardrails

- Follow the current stack; do not add dependencies without approval.
- Do not put credentials or sensitive document content in logs, examples, fixtures or generated reports.
- Do not allow model output to decide identity, ACL, approval, citation validity or final business state.
- Do not tune against a sealed holdout set. If the holdout was inspected, mark it contaminated and create a new one.
- Use synthetic data only when clearly labeled; never claim synthetic results as production impact.
- Preserve existing contracts. Breaking changes require a new version and migration plan.

### 6. Closeout

Before marking complete:
- run the specified tests and record actual outcomes;
- update `CHANGELOG.md` after reviewing the real diff;
- update roadmap status only when its acceptance gate passes;
- save evaluation reports and evidence in the project’s established locations;
- report what was verified, what was not verified, and any deployment risks.

## Output format

Return the spec directory path and a short summary of scope, files created, validation commands, open decisions, and Definition of Done. Never claim tests passed unless they were actually run.
