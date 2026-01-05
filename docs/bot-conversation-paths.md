## Bot conversation paths

This bot uses Telegram batching + a worker pipeline to keep UX responsive while controlling cost.

### Positive paths (desired)
- **On-topic, short request**: user sends restaurant/outlet question → optional short backchannel ack → bot asks 1 clarifying question (strict intake) or responds minimally.
- **On-topic, multi-message**: user sends multiple messages → worker processes as one session → optional ack → bot responds once per session.
- **On-topic with context**: bot loads `telegram_chat_memory.summary_text` + last 50 messages (user + bot) to keep continuity across sessions.

### Negative paths (expected + controlled)
- **Off-topic**: bot sends a single redirect message, sets off-topic mode (`telegram_chat_states.off_topic_mode=true`), then ghosts until the user becomes on-topic again.
- **Ack skipped**: to avoid feeling robotic, `send_session_ack` may intentionally produce no message.
- **Transient provider issues**: expensive replies retry via Celery backoff; cost details can be backfilled later via OpenRouter `/generation`.

### Data/telemetry
- Incoming messages: `telegram_messages`
- Outgoing messages: `telegram_outgoing_messages` (`kind`: `ack|redirect|reply`)
- LLM call telemetry: `llm_calls` (`purpose`: `ack|gate|reply|memory`)
- Chat state: `telegram_chat_states` (off-topic ghosting mode)
- Memory summary: `telegram_chat_memory`
