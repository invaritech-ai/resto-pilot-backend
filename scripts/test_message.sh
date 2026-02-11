#!/usr/bin/env bash
#
# Test message helper - send messages to test API without Telegram
#
# Usage:
#   ./scripts/test_message.sh <telegram_id> "<message>" [console_mode]
#
# Examples:
#   ./scripts/test_message.sh 123456789 "show my profile"
#   ./scripts/test_message.sh 123456789 "list my outlets" true
#   ./scripts/test_message.sh 123456789 "create outlet Downtown Kitchen" false
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load environment from .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Check required vars
if [ -z "$SIDE_CHANNEL_SECRET_TOKEN" ]; then
  echo -e "${RED}Error: SIDE_CHANNEL_SECRET_TOKEN not set in .env${NC}"
  echo "Run: echo \"SIDE_CHANNEL_SECRET_TOKEN=\$(openssl rand -hex 32)\" >> .env"
  exit 1
fi

# Parse args
TELEGRAM_ID=${1:-$DEFAULT_TEST_TELEGRAM_ID}
MESSAGE=${2:-"show my profile"}
CONSOLE_MODE=${3:-true}
API_URL=${API_URL:-http://localhost:8000}

if [ -z "$TELEGRAM_ID" ]; then
  echo -e "${YELLOW}Usage: $0 <telegram_id> <message> [console_mode]${NC}"
  echo ""
  echo "Examples:"
  echo "  $0 123456789 \"show my profile\""
  echo "  $0 123456789 \"list my outlets\" true"
  echo "  $0 123456789 \"create outlet Main Kitchen\" false"
  echo ""
  echo "To find your telegram_id, run:"
  echo "  psql \$APP_DATABASE_URL -c \"SELECT telegram_id, full_name FROM users;\""
  exit 1
fi

# Make request
echo -e "${GREEN}Sending test message...${NC}"
echo "Telegram ID: $TELEGRAM_ID"
echo "Message: $MESSAGE"
echo "Console mode: $CONSOLE_MODE"
echo ""

RESPONSE=$(curl -s -X POST "$API_URL/api/v1/test/message" \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SIDE_CHANNEL_SECRET_TOKEN" \
  -d "{
    \"telegram_id\": $TELEGRAM_ID,
    \"message\": \"$MESSAGE\",
    \"console_mode\": $CONSOLE_MODE
  }")

# Check if request succeeded
if echo "$RESPONSE" | jq -e '.detail' > /dev/null 2>&1; then
  echo -e "${RED}Error:${NC}"
  echo "$RESPONSE" | jq '.detail'
  exit 1
fi

# Pretty print response
echo -e "${GREEN}Response:${NC}"
echo "$RESPONSE" | jq .

# Extract key fields
echo ""
echo -e "${GREEN}Key Fields:${NC}"
echo -e "  Action: $(echo "$RESPONSE" | jq -r '.planner_decision.action')"
echo -e "  Tool: $(echo "$RESPONSE" | jq -r '.planner_decision.tool // "N/A"')"
echo -e "  Validation: $(echo "$RESPONSE" | jq -r '.validation_passed')"
echo -e "  Tool executed: $(echo "$RESPONSE" | jq -r '.tool_executed')"
echo -e "  Response text: $(echo "$RESPONSE" | jq -r '.response_text | split("\n")[0]')..."
echo ""
echo -e "${GREEN}LLM Metrics:${NC}"
echo -e "  Planner: $(echo "$RESPONSE" | jq -r '.planner_model // "N/A"') - $(echo "$RESPONSE" | jq -r '.planner_tokens.total_tokens // 0') tokens in $(echo "$RESPONSE" | jq -r '.planner_latency_ms // 0')ms"
echo -e "  Presenter: $(echo "$RESPONSE" | jq -r '.presenter_model // "N/A"') - $(echo "$RESPONSE" | jq -r '.presenter_tokens.total_tokens // 0') tokens in $(echo "$RESPONSE" | jq -r '.presenter_latency_ms // 0')ms"
