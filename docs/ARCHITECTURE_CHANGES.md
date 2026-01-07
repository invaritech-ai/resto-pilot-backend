# Architecture Changes: Capability System → General-Purpose Agent

## Summary

The bot architecture was refactored from a capability-gated system to a general-purpose agent with tool-calling support.

## What Changed

### Removed Components

-   `app/ai/capability_gate.py` - Capability classification and gating
-   `app/ai/reply_guard.py` - Output validation guard
-   `app/ai/db_router.py` - Database routing logic
-   `app/ai/session_reply.py` - Old reply generation

### Added Components

-   `app/ai/agent.py` - General-purpose agent loop with tool-calling
-   `app/ai/db_tools.py` - Database tools for the agent
-   `app/ai/tools.py` - Base tool infrastructure

### Updated Components

-   `app/processing/session_processor.py` - Now uses `run_agent_loop()` instead of capability gates
-   `app/ai/topic_gate.py` - Updated to be more general-purpose (removed capability-specific logic)

## Key Improvements

1. **Autonomous Decision-Making**: The agent can now decide which tools to use based on user requests, without hardcoded capability restrictions.

2. **Better Context Handling**: The agent can map restaurant names to IDs, create restaurants if they don't exist, and maintain context across conversations.

3. **Granular Cost Tracking**: Every LLM call in the agent loop is recorded individually in `llm_calls` with `openrouter_generation_id` for accurate cost attribution and backfill.

4. **Access Control**: Role/scope-based access control is enforced at the tool level via `app/policies/db_policy.py`, providing fine-grained security without restricting functionality.

## Telemetry & Cost Tracking

**Critical**: Every LLM call is now recorded individually:

-   Agent loop rounds: `purpose = agent_round_N_tool` or `agent_round_N_final`
-   Topic gate: `purpose = gate`
-   Memory update: `purpose = memory`
-   Backchannel ack: `purpose = ack_message`

Each call with an `openrouter_generation_id` automatically schedules cost backfill via Celery.

## Migration Guide

For developers working with the codebase:

1. **Access Control**: Use `app/policies/db_policy.py` to enforce permissions, not capability gates
2. **Adding Functionality**: Add new tools to `app/ai/db_tools.py` or create new tool files
3. **Cost Tracking**: All LLM calls are automatically tracked - ensure `session_id` and `chat_id` are passed to `run_agent_loop()`

## Documentation Updates

-   `README.md` - Removed capability references, updated LLM description
-   `docs/capabilities.md` - Marked as deprecated with migration notes
-   `docs/resto-pilot-codebase-summary.md` - Added agent architecture section
-   `docs/bot-conversation-paths.md` - Updated to reflect general-purpose agent
-   `docs/unified-message-flow.md` - Updated processing pipeline description
-   `docs/journeys.md` - Updated platform constraints section
-   `docs/unified-flow-refactor-checklist.md` - Marked capability sections as completed/replaced
-   `docs/llm-ack-gating-worklist.md` - Added deprecation notes

## Date

January 2025
