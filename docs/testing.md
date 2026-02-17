# Testing Guide

This guide explains how to test the resto-pilot bot without needing to send messages through Telegram.

---

## Quick Start

### 1. Set up your environment

```bash
# Copy .env.example to .env if you haven't
cp .env.example .env

# Set your side-channel secret (for test API security)
echo "SIDE_CHANNEL_SECRET_TOKEN=$(openssl rand -hex 32)" >> .env

# Enable deterministic execution (Phase 1+)
echo "DETERMINISTIC_EXECUTION=true" >> .env

# Set up database
alembic upgrade head
```

### 2. Start the API

```bash
# Option 1: Using the script
./scripts/run_api.sh

# Option 2: Using uv directly
uv run uvicorn app.main:app --reload
```

### 3. Find your Telegram ID

First, send a message to your bot on Telegram (e.g., `/start`). Then check the database:

```bash
# Using psql
psql $APP_DATABASE_URL -c "SELECT telegram_id, full_name FROM users;"

# Or query via Python
uv run python -c "
from app.core.config import get_settings
from app.db.session import get_sync_session_context
from app.db.models.user import User
from sqlalchemy import select

settings = get_settings()
with get_sync_session_context(settings) as db:
    users = db.scalars(select(User)).all()
    for u in users:
        print(f'{u.telegram_id} - {u.full_name}')
"
```

### 4. Test via API

```bash
# Get your secret from .env
export SECRET=$(grep SIDE_CHANNEL_SECRET_TOKEN .env | cut -d'=' -f2)
export TELEGRAM_ID=123456789  # Replace with your telegram_id

# Test a simple query
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"show my profile\",
    \"console_mode\": true
  }" | jq .
```

---

## Test API Endpoints

### `/api/v1/test/message` (Synchronous, Phase 0+)

**Purpose:** Test messages synchronously without Telegram, get full decision trace

**Request:**
```json
{
  "telegram_id": 123456789,
  "message": "show my profile",
  "console_mode": true,
  "restaurant_id": "uuid-optional"
}
```

**Response:**
```json
{
  "user_id": "user-uuid",
  "telegram_id": 123456789,
  "chat_id": 0,

  "planner_decision": {
    "action": "call_tool",
    "tool": "profile_get",
    "args": {}
  },
  "planner_model": "openai/gpt-4o-mini",
  "planner_latency_ms": 450,
  "planner_tokens": {
    "prompt_tokens": 200,
    "completion_tokens": 15,
    "total_tokens": 215
  },

  "validation_errors": [],
  "validation_passed": true,

  "tool_executed": true,
  "tool_result": {
    "full_name": "John Doe",
    "phone": "+1234567890",
    "username": "johndoe"
  },
  "tool_error": null,

  "presenter_model": "openai/gpt-4o-mini",
  "presenter_latency_ms": 320,
  "presenter_tokens": {
    "prompt_tokens": 50,
    "completion_tokens": 30,
    "total_tokens": 80
  },

  "response_text": "Your profile:\n\nName: John Doe\nPhone: +1234567890\nUsername: @johndoe",

  "console_output": [
    "[BOT] Your profile:\\n\\nName: John Doe\\nPhone: +1234567890\\nUsername: @johndoe"
  ]
}
```

**Features:**
- ✅ Synchronous (no Celery)
- ✅ Full decision trace
- ✅ Console mode support
- ✅ Uses real test/dev database
- ✅ Returns all LLM metrics

### `/api/v1/sidechannel/message` (Async, Existing)

**Purpose:** Test via Celery worker (async, like production)

**Request:**
```json
{
  "telegram_id": "console-log",
  "user_telegram_id": 123456789,
  "text": "show my profile",
  "mode": "enqueue"
}
```

**Use when:**
- Testing Celery integration
- Testing async workflows
- Simulating production behavior

**Note:** Output appears in worker logs, not API response

---

## Common Test Scenarios

### Profile Operations

```bash
# View profile
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"show my profile\",
    \"console_mode\": true
  }" | jq '.response_text'

# Update name
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"update my name to Jane Smith\",
    \"console_mode\": true
  }" | jq '.response_text'
```

### Restaurant Operations

```bash
# List restaurants
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"list my outlets\",
    \"console_mode\": true
  }" | jq '.response_text'

# Create restaurant
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"create outlet Downtown Kitchen\",
    \"console_mode\": true
  }" | jq '.response_text'

# Select active restaurant
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"switch to Downtown Kitchen\",
    \"console_mode\": true
  }" | jq '.response_text'
```

### Supplier Operations

```bash
# List suppliers
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"list suppliers\",
    \"console_mode\": true
  }" | jq '.response_text'

# Create supplier
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"add supplier Fresh Farms\",
    \"console_mode\": true
  }" | jq '.response_text'
```

### Clarification Flow

```bash
# Ambiguous query (should ask for clarification)
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SECRET" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"update kitchen\",
    \"console_mode\": true
  }" | jq '.planner_decision'

# Expected: clarification with numbered choices
```

---

## Analyzing Responses

### Check if planner decided correctly

```bash
# Extract planner decision
curl -s ... | jq '.planner_decision.action'  # Should be "call_tool" or "clarify"
curl -s ... | jq '.planner_decision.tool'    # Tool name
curl -s ... | jq '.planner_decision.args'    # Tool arguments
```

### Check validation

```bash
# Check if validation passed
curl -s ... | jq '.validation_passed'     # Should be true
curl -s ... | jq '.validation_errors'     # Should be []

# Check for UUID leakage
curl -s ... | jq '.planner_decision.args' | grep -E '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
# Should return nothing (no UUIDs in args)
```

### Check LLM costs

```bash
# Extract token usage
curl -s ... | jq '.planner_tokens'
curl -s ... | jq '.presenter_tokens'

# Calculate total tokens
curl -s ... | jq '(.planner_tokens.total_tokens // 0) + (.presenter_tokens.total_tokens // 0)'
```

### Check execution results

```bash
# Check if tool executed successfully
curl -s ... | jq '.tool_executed'    # Should be true
curl -s ... | jq '.tool_error'       # Should be null
curl -s ... | jq '.tool_result'      # Tool output data
```

---

## Testing Scripts

### Create a test helper script

Save this as `scripts/test_message.sh`:

```bash
#!/usr/bin/env bash
set -e

# Load environment
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Check required vars
if [ -z "$SIDE_CHANNEL_SECRET_TOKEN" ]; then
  echo "Error: SIDE_CHANNEL_SECRET_TOKEN not set in .env"
  exit 1
fi

# Parse args
TELEGRAM_ID=${1:-$DEFAULT_TEST_TELEGRAM_ID}
MESSAGE=${2:-"show my profile"}
CONSOLE_MODE=${3:-true}

if [ -z "$TELEGRAM_ID" ]; then
  echo "Usage: $0 <telegram_id> <message> [console_mode]"
  echo "Example: $0 123456789 \"show my profile\" true"
  exit 1
fi

# Make request
curl -X POST http://localhost:8000/api/v1/test/message \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SIDE_CHANNEL_SECRET_TOKEN" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"$MESSAGE\",
    \"console_mode\": $CONSOLE_MODE
  }" | jq .
```

Make it executable:
```bash
chmod +x scripts/test_message.sh
```

Usage:
```bash
./scripts/test_message.sh 123456789 "show my profile"
./scripts/test_message.sh 123456789 "list my outlets"
./scripts/test_message.sh 123456789 "create outlet Main Kitchen"
```

---

## Integration Testing

### Test file (Phase 0, Task 3)

See `tests/integration/test_deterministic_flow.py` for automated integration tests.

Run with:
```bash
pytest tests/integration/test_deterministic_flow.py -v
```

---

## Debugging Tips

### View planner prompt

Add logging to see what the planner receives:

```python
# In app/ai/deterministic/planner.py
logger.info("planner_input", extra={"payload": payload})
```

### View tool execution

Add logging to see tool execution:

```python
# In app/ai/deterministic/execution.py
logger.info("tool_execute", extra={"tool": tool, "args": args, "result": result})
```

### Check database state

```bash
# View recent LLM calls
psql $APP_DATABASE_URL -c "
SELECT purpose, model, total_tokens, latency_ms, requested_at
FROM llm_calls
ORDER BY requested_at DESC
LIMIT 10;
"

# View recent outgoing messages
psql $APP_DATABASE_URL -c "
SELECT kind, substring(text, 1, 50) as message, sent_at
FROM telegram_outgoing_messages
ORDER BY sent_at DESC
LIMIT 10;
"
```

---

## Next Steps

- **Phase 1:** Test all workflows after enabling `DETERMINISTIC_EXECUTION=true`
- **Phase 2:** Test inventory reconciliation tools
- **Phase 3:** Test lookup tools and multi-step execution
- **Phase 4:** Test ack system
- **Phase 5:** Test hybrid clarification parsing
- **Phase 6:** Verify legacy code removal
- **Phase 7:** Test advanced search features

See [MIGRATION_PLAN.md](MIGRATION_PLAN.md) for full details.
