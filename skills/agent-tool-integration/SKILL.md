---
name: agent-tool-integration
description: Designs and implements safe, auditable tool integrations for RAG/Agent applications, including REST APIs, databases, and MCP servers.
---

# Agent Tool Integration Skill

## Purpose

Make tool use explicit, constrained, testable and auditable. The model may request a tool call, but the application and server-side policy decide whether it is allowed and execute it.

## Workflow

### 1. Decide whether a tool is needed

Document the user task, information not available in the knowledge base, data freshness needs, risk of external effects, and cancellation conditions. Prefer no tool when static retrieval is sufficient.

### 2. Define a tool contract before implementation

For every tool, specify:
- stable name and version;
- purpose and negative list (what it must never do);
- effect category: `read_only`, `write_preview`, or `write_execute`;
- closed input and output schema; reject unexpected fields;
- trusted identity and authorization requirements;
- tenant/site/resource scoping;
- timeout/deadline, result-size limit and rate limit;
- error categories and retry policy;
- audit/trace fields;
- idempotency and reconciliation rules if it can write.

### 3. Keep trust boundaries server-side

- Never trust role, tenant, approval or user identity supplied in model-generated arguments.
- Derive identity from the authenticated session and enforce authorization again in the tool server.
- Filter database rows by ACL before returning results.
- Return a generic `not_available` where distinguishing nonexistent from unauthorized resources would leak information.
- Treat tool outputs and retrieved documents as untrusted data, not instructions.

### 4. Implement read-only tools first

Use narrow tools such as `get_work_order_status`, `get_equipment_status`, `get_interface_health` and `search_incident_history`. Return the minimum fields required. Validate the result schema before adding it to model context.

Test success, empty result, invalid arguments, extra fields, unauthorized request, timeout, dependency outage, oversized response and malicious content in returned text.

### 5. Add MCP only when useful

MCP can standardize tool/resource access for multiple clients. It does not provide business authorization by itself and does not replace API contracts. Keep authorization and validation inside the server regardless of whether the caller uses REST, an in-process function or MCP.

### 6. Write paths require a separate gate

For any external effect, implement:
1. preview with normalized parameters;
2. structured approval bound to actor, target, action, parameters hash, policy version, expiry and run ID;
3. idempotent execution using a stable idempotency key;
4. independent read-back verification and effect ledger.

If execution outcome is unknown, query the external system before retrying. Never convert unknown into success. Revocation and cancellation must be checked server-side immediately before execution.

### 7. Observability and tests

Emit redacted spans for request, identity, policy, tool selection, tool execution, result validation, approval, external effect and reconciliation. Record status, error class, version identifiers, counts and latency, not secrets or full sensitive payloads.

Add tests proving:
- unauthorized calls cause zero effects;
- malformed input is rejected;
- repeated idempotency key does not duplicate effects;
- failed or unknown writes are reconciled;
- prompt injection cannot grant permissions;
- tool-off and model-off modes fail safely.

### 8. Definition of Done

- Tool contract and negative list reviewed.
- Server-side authorization enforced and tested.
- Input/output schemas closed and versioned.
- Timeouts, result limits and error semantics implemented.
- Logs are redacted and traceable.
- Read-only path is independently verified before any write path.
- For writes: approval, idempotency, read-back verification and effect accounting all pass.
- README and changelog updated; actual test evidence and limitations recorded.
