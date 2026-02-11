#!/usr/bin/env bash
#
# Test all happy path commands via deterministic flow
#
# Usage: ./scripts/test_happy_paths.sh <telegram_id>
#

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Load environment
if [ -f .env ]; then
  export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

TELEGRAM_ID=${1:-$DEFAULT_TEST_TELEGRAM_ID}
API_URL=${API_URL:-http://localhost:8000}

if [ -z "$TELEGRAM_ID" ]; then
  echo "Usage: $0 <telegram_id>"
  exit 1
fi

if [ -z "$SIDE_CHANNEL_SECRET_TOKEN" ]; then
  echo "Error: SIDE_CHANNEL_SECRET_TOKEN not set in .env"
  exit 1
fi

function test_message() {
  local msg="$1"
  echo -e "${CYAN}Testing: $msg${NC}"

  curl -s -X POST "$API_URL/api/v1/test/message" \
    -H "Content-Type: application/json" \
    -H "X-Side-Channel-Secret-Token: $SIDE_CHANNEL_SECRET_TOKEN" \
    -d "{
      \"telegram_id\": $TELEGRAM_ID,
      \"message\": \"$msg\",
      \"console_mode\": true
    }" | jq -r '.status, .task_id' | head -1

  echo -e "${GREEN}✓ Enqueued${NC}"
  echo "  Check worker logs for output"
  echo ""
  sleep 2
}

echo -e "${GREEN}=== Testing Happy Paths ===${NC}"
echo "Telegram ID: $TELEGRAM_ID"
echo "Watch worker logs (Terminal 2) for responses"
echo ""
sleep 2

echo -e "${YELLOW}--- Profile Operations ---${NC}"
test_message "show my profile"
test_message "update my name to Test User"
test_message "show my profile"

echo -e "${YELLOW}--- Restaurant Operations ---${NC}"
test_message "list my restaurants"
test_message "list my outlets"
test_message "create outlet Test Kitchen"
test_message "list my outlets"
test_message "switch to Test Kitchen"
test_message "rename this outlet to Main Kitchen"

echo -e "${YELLOW}--- Supplier Operations ---${NC}"
test_message "list suppliers"
test_message "add supplier Fresh Produce Co"
test_message "list suppliers"
test_message "view Fresh Produce"

echo -e "${YELLOW}--- Staff Operations ---${NC}"
test_message "list staff"
test_message "invite staff to Main Kitchen"

echo -e "${GREEN}=== All Tests Enqueued ===${NC}"
echo "Review worker logs to verify all responses"
