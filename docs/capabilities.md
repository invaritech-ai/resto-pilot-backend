# ⚠️ DEPRECATED: Capability System

**This document is outdated.** The capability gating system has been removed in favor of a general-purpose agent with tool-calling.

## Current Architecture

The bot now uses a **general-purpose agent loop** (`app/ai/agent.py`) that:

-   Supports autonomous tool-calling for database operations
-   Enforces access control via role/scope-based policies (`app/policies/db_policy.py`)
-   Records every LLM call individually for cost tracking
-   Can handle diverse user requests without hardcoded capability restrictions

## Migration Notes

The following components were removed:

-   `app/ai/capability_gate.py` - capability classification
-   `app/ai/reply_guard.py` - output validation guard
-   `app/ai/db_router.py` - database routing logic
-   `app/ai/session_reply.py` - old reply generation

The following components were added:

-   `app/ai/agent.py` - general-purpose agent loop with tool-calling
-   `app/ai/db_tools.py` - database tools for the agent
-   `app/ai/tools.py` - base tool infrastructure

## Access Control

Access control is now enforced at the tool level:

-   **Role-based**: Users have roles (owner/staff) that determine which tables/operations they can access
-   **Scope-based**: Operations are scoped to restaurants the user owns/manages
-   **Policy enforcement**: `app/policies/db_policy.py` validates all database actions before execution

See `app/policies/db_allowlist.py` for the current allowlist configuration.

---

**For historical reference only.** This document describes the old capability system that was removed.
