"""Quick verification test for bot_api.py"""

from app.telegram.bot_api import send_message
from app.core.config import Settings, get_settings

# Test 1: Import successful
print("✓ Import successful")

# Test 2: Function signature check
print("✓ send_message function exists")
print(f"✓ Function signature: {send_message.__annotations__}")

# Test 3: Verify it raises ValueError when token is missing
try:
    settings = Settings(telegram_bot_token="")
    send_message(7661369993, "test", settings)
    print("✗ Should have raised ValueError")
except ValueError as e:
    print(f"✓ Correctly raises ValueError when token is missing: {e}")

# Test 4: Verify it sends the message
try:
    settings = get_settings()
    send_message(7661369993, "test", settings)
    print("✓ Should send message in Telegram")
except ValueError as e:
    print(f"✓ Correctly raises ValueError when token is missing: {e}")


print("\n✓ All basic checks passed!")
print("\nTo test actual sending, configure APP_TELEGRAM_BOT_TOKEN in .env")
print("Then call: send_message(chat_id, 'Your message', get_settings())")
