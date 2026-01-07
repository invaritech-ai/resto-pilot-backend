## Bot conversation paths

This bot uses Telegram batching + a worker pipeline to keep UX responsive while controlling cost.

### Positive paths (desired)

-   **On-topic, short request**: user sends restaurant/outlet question → optional short backchannel ack → agent uses tools to query database/restaurants → bot responds with results.
-   **On-topic, multi-message**: user sends multiple messages → worker processes as one session → optional ack → agent processes with tool-calling → bot responds once per session.
-   **On-topic with context**: bot loads `telegram_chat_memory.summary_text` + last 50 messages (user + bot) to keep continuity across sessions.
-   **Tool-calling conversations**: agent autonomously decides which tools to use (e.g., "list available tables", "find restaurant by name", "create restaurant") and executes them in a multi-round conversation.

### Negative paths (expected + controlled)

-   **Off-topic**: bot sends a single redirect message, sets off-topic mode (`telegram_chat_states.off_topic_mode=true`), then ghosts until the user becomes on-topic again.
-   **Access denied**: if user lacks permission for a requested operation, agent returns an error message explaining the restriction.
-   **Ack skipped**: to avoid feeling robotic, `send_session_ack` may intentionally produce no message.
-   **Transient provider issues**: expensive replies retry via Celery backoff; cost details can be backfilled later via OpenRouter `/generation`.

### Data/telemetry

-   Incoming messages: `telegram_messages`
-   Outgoing messages: `telegram_outgoing_messages` (`kind`: `ack|ack_message|redirect|reply`)
-   LLM call telemetry: `llm_calls` (`purpose`: `ack|ack_message|gate|agent_round_N_tool|agent_round_N_final|memory`)
    -   Each LLM call in the agent loop is recorded individually with `openrouter_generation_id` for cost attribution
    -   Cost backfill is scheduled automatically for each call with a generation ID
-   Chat state: `telegram_chat_states` (off-topic ghosting mode)
-   Memory summary: `telegram_chat_memory`
