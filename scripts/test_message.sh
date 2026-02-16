#!/usr/bin/env bash
#
# Test message helper - send messages to test API without Telegram
#
# Usage:
#   ./scripts/test_message.sh <telegram_id> "<message>" [console_mode] [file_path]
#
# Examples:
#   ./scripts/test_message.sh 123456789 "show my profile"
#   ./scripts/test_message.sh 123456789 "list my outlets" true
#   ./scripts/test_message.sh 123456789 "price list for Main Kitchen" true "/Users/you/Downloads/PriceList.pdf"
#   ./scripts/test_message.sh 123456789 "invoice from supplier" true "/Users/you/Downloads/Invoice.pdf"
#   ./scripts/test_message.sh 123456789 "inventory photo" true "/Users/you/Downloads/inventory.jpg"
#
# Note: File path should be an absolute path to a local file for testing file processing
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load environment from .env
if [ -f .env ]; then
  set -a
  source .env
  set +a
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
FILE_PATH=${4:-}
API_URL=${API_URL:-http://localhost:8000}

if [ -z "$TELEGRAM_ID" ]; then
  echo -e "${YELLOW}Usage: $0 <telegram_id> <message> [console_mode] [file_path]${NC}"
  echo ""
  echo "Examples:"
  echo "  $0 123456789 \"show my profile\""
  echo "  $0 123456789 \"list my outlets\" true"
  echo "  $0 123456789 \"price list\" true \"/Users/you/Downloads/PriceList.pdf\""
  echo "  $0 123456789 \"invoice\" true \"/Users/you/Downloads/Invoice.pdf\""
  echo "  $0 123456789 \"inventory photo\" true \"/Users/you/Photos/inventory.jpg\""
  echo ""
  echo "Note: File path must be an absolute path to a local file"
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
if [ -n "$FILE_PATH" ]; then
  echo "File path: $FILE_PATH"
fi
echo ""

# Build JSON payload
if [ -n "$FILE_PATH" ]; then
  JSON_PAYLOAD=$(cat <<EOF
{
  "telegram_id": $TELEGRAM_ID,
  "message": "$MESSAGE",
  "console_mode": $CONSOLE_MODE,
  "file_path": "$FILE_PATH"
}
EOF
)
else
  JSON_PAYLOAD=$(cat <<EOF
{
  "telegram_id": $TELEGRAM_ID,
  "message": "$MESSAGE",
  "console_mode": $CONSOLE_MODE
}
EOF
)
fi

RESPONSE=$(curl -s -X POST "$API_URL/api/v1/test/message" \
  -H "Content-Type: application/json" \
  -H "X-Side-Channel-Secret-Token: $SIDE_CHANNEL_SECRET_TOKEN" \
  -d "$JSON_PAYLOAD")

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
echo -e "${GREEN}Task Info:${NC}"
echo -e "  Status: $(echo "$RESPONSE" | jq -r '.status')"
echo -e "  Task ID: $(echo "$RESPONSE" | jq -r '.task_id // "N/A"')"
echo -e "  Chat ID: $(echo "$RESPONSE" | jq -r '.chat_id')"
echo -e "  Console mode: $(echo "$RESPONSE" | jq -r '.console_mode')"
echo -e "  Note: $(echo "$RESPONSE" | jq -r '.note')"
echo ""
echo -e "${YELLOW}Check worker logs (Terminal 2) for the bot's response${NC}"
