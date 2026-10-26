---
name: rag-evaluation
description: Creates and runs reproducible RAG and Agent evaluations, including retrieval comparisons, citation checks, refusal tests, tool-call tests, and regression reports.
---

# RAG / Agent Evaluation Skill

## Objective

Evaluate system behaviour with reproducible evidence. Separate retrieval quality, answer quality, policy/security correctness, and runtime health. A single aggregate score is insufficient.

## Workflow

### 1. Inspect before measuring

Read the roadmap, current evaluation plan, corpus/index manifests, retrieval/generation contracts, role-permission matrix, prior reports, and relevant tests. Record code revision and all relevant configuration versions.

### 2. Define or verify the evaluation plan

Before running candidate comparisons, define:
- target decision the evaluation supports;
- dataset and split: development, calibration, holdout;
- case categories and expected outcomes;
- metric definitions and thresholds;
- failure handling and promotion/demotion criteria;
- environment/model variability and repeat policy.

Do not modify a metric or threshold after viewing candidate results without creating a new versioned plan and documenting the change.

### 3. Build a balanced case set

Include at minimum:
- answerable direct questions;
- multi-chunk or multi-document questions;
- no-answer cases;
- stale or conflicting sources;
- restricted/unauthorized content;
- paraphrase, typo and bilingual queries;
- prompt injection in user query and retrieved content;
- tool timeout, malformed tool result and unavailable dependency for Agent cases.

Each case should have a stable `case_id`, user role, query, expected terminal state, gold evidence/source IDs, category and annotation rationale. Do not include hidden labels in the model input.

### 4. Retrieval evaluation

Compare keyword, vector, hybrid and reranked retrieval under the same corpus snapshot, ACL policy, queries and cutoff K. Track:
- Recall@K;
- MRR or nDCG@K;
- empty retrieval rate;
- unauthorized candidate count (must be zero);
- latency and failure rate;
- category-level breakdowns.

Persist per-case results (JSONL), aggregate report, configuration manifest and input hashes.

### 5. Generation and citation evaluation

Evaluate separately:
- answer correctness against evidence;
- citation support/entailment under an explicit rubric;
- citation resolvability: cited IDs must exist in this run's candidate set and match source version;
- correct refusal for no-answer, stale, conflicting or unauthorized cases;
- unsupported claims and overconfident root-cause claims;
- structured-output/schema validity.

Use deterministic code for citation membership, source/version matching, ACL checks and schema validation. LLM-as-judge may be used as a supplemental signal, not as the only acceptance gate.

### 6. Agent evaluation

Track:
- task completion rate;
- correct tool selection and argument validity;
- unnecessary tool calls;
- tool error handling and timeout behaviour;
- policy violations and unauthorized calls;
- duplicate external effects (must be zero in tests);
- correct escalation and terminal-state mapping;
- latency and cost where measurable.

For write tools, assert no external effect without a valid structured approval, and assert idempotent replay does not create a second effect.

### 7. Freeze and protect holdout

Record SHA-256 for dataset, labels, manifest and configuration. Run the holdout only under the project’s approved procedure. If results are used for tuning, disclose contamination and replace the holdout for subsequent independent evaluation.

### 8. Failure analysis

Assign each failure to one or more layers:
- corpus/ingestion;
- metadata/ACL;
- retrieval;
- ranking/context construction;
- generation;
- citation validation;
- refusal/escalation;
- tool selection/execution;
- product workflow/runtime.

Record `case_id`, expected/actual outcome, root-cause hypothesis, evidence, fix proposal and regression-test ID. Do not quietly delete failed cases.

### 9. Report honestly

Report:
- dataset and system versions;
- metrics with numerator/denominator and category breakdowns;
- actual test commands and outcomes;
- known limitations and synthetic-data caveats;
- whether the evaluation was independent;
- what has not been proven (for example production ROI, real traffic SLO or user adoption).

Never invent scores or present target thresholds as measured results.
