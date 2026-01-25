#!/usr/bin/env python3
"""
Test script for Telegram file error handling.

Simulates different error scenarios to verify user-friendly error messages.
"""

import sys
from unittest.mock import patch, MagicMock

from app.telegram.bot_api import (
    TelegramFileExpiredError,
    TelegramFileNetworkError,
    TelegramFileTooBigError,
    get_file_bytes,
)
from app.core.config import get_settings


def test_file_expired_404():
    """Simulate Telegram returning 404 - file expired."""
    print("=" * 80)
    print("TEST 1: File Expired (404)")
    print("=" * 80)

    settings = get_settings()

    with patch("app.telegram.bot_api.httpx.get") as mock_get:
        # Simulate 404 response
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )
        mock_get.return_value = mock_response

        try:
            get_file_bytes(file_id="expired_file_123", settings=settings)
            print("❌ FAILED: Should have raised TelegramFileExpiredError")
        except TelegramFileExpiredError as e:
            print(f"✅ PASSED: Raised TelegramFileExpiredError")
            print(f"   Message: {str(e)}")
        except Exception as e:
            print(f"❌ FAILED: Raised wrong exception: {type(e).__name__}: {e}")

    print()


def test_file_too_big():
    """Simulate file exceeding size limit during download."""
    print("=" * 80)
    print("TEST 2: File Too Big (>20MB)")
    print("=" * 80)

    settings = get_settings()

    with patch("app.telegram.bot_api.httpx") as mock_httpx:
        # Mock successful getFile response
        mock_get_response = MagicMock()
        mock_get_response.raise_for_status = MagicMock()
        mock_get_response.json.return_value = {
            "ok": True,
            "result": {"file_path": "documents/file123.pdf"},
        }
        mock_httpx.get.return_value = mock_get_response

        # Mock file download stream that exceeds limit
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.raise_for_status = MagicMock()
        # Simulate 25MB of data (exceeds 20MB limit)
        mock_stream.iter_bytes.return_value = [b"x" * 1024 * 1024] * 25  # 25 chunks of 1MB
        mock_httpx.stream.return_value = mock_stream

        try:
            get_file_bytes(file_id="big_file_123", settings=settings)
            print("❌ FAILED: Should have raised TelegramFileTooBigError")
        except TelegramFileTooBigError as e:
            print(f"✅ PASSED: Raised TelegramFileTooBigError")
            print(f"   Message: {str(e)}")
        except Exception as e:
            print(f"❌ FAILED: Raised wrong exception: {type(e).__name__}: {e}")

    print()


def test_network_timeout():
    """Simulate network timeout during download."""
    print("=" * 80)
    print("TEST 3: Network Timeout")
    print("=" * 80)

    settings = get_settings()

    with patch("app.telegram.bot_api.httpx.get") as mock_get:
        # Simulate timeout
        mock_get.side_effect = __import__("httpx").TimeoutException("Timeout")

        try:
            get_file_bytes(file_id="timeout_file_123", settings=settings)
            print("❌ FAILED: Should have raised TelegramFileNetworkError")
        except TelegramFileNetworkError as e:
            print(f"✅ PASSED: Raised TelegramFileNetworkError")
            print(f"   Message: {str(e)}")
        except Exception as e:
            print(f"❌ FAILED: Raised wrong exception: {type(e).__name__}: {e}")

    print()


def test_file_403_forbidden():
    """Simulate Telegram returning 403 - bot doesn't have access."""
    print("=" * 80)
    print("TEST 4: File Forbidden (403)")
    print("=" * 80)

    settings = get_settings()

    with patch("app.telegram.bot_api.httpx.get") as mock_get:
        # Simulate 403 response
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "Forbidden", request=MagicMock(), response=mock_response
        )
        mock_get.return_value = mock_response

        try:
            get_file_bytes(file_id="forbidden_file_123", settings=settings)
            print("❌ FAILED: Should have raised TelegramFileExpiredError")
        except TelegramFileExpiredError as e:
            print(f"✅ PASSED: Raised TelegramFileExpiredError")
            print(f"   Message: {str(e)}")
        except Exception as e:
            print(f"❌ FAILED: Raised wrong exception: {type(e).__name__}: {e}")

    print()


def test_server_error_500():
    """Simulate Telegram server error (500)."""
    print("=" * 80)
    print("TEST 5: Server Error (500)")
    print("=" * 80)

    settings = get_settings()

    with patch("app.telegram.bot_api.httpx.get") as mock_get:
        # Simulate 500 response
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "Internal Server Error", request=MagicMock(), response=mock_response
        )
        mock_get.return_value = mock_response

        try:
            get_file_bytes(file_id="server_error_file_123", settings=settings)
            print("❌ FAILED: Should have raised TelegramFileNetworkError")
        except TelegramFileNetworkError as e:
            print(f"✅ PASSED: Raised TelegramFileNetworkError")
            print(f"   Message: {str(e)}")
        except Exception as e:
            print(f"❌ FAILED: Raised wrong exception: {type(e).__name__}: {e}")

    print()


def main():
    """Run all tests."""
    print("\n")
    print("=" * 80)
    print("TELEGRAM FILE ERROR HANDLING TESTS")
    print("=" * 80)
    print()

    test_file_expired_404()
    test_file_403_forbidden()
    test_file_too_big()
    test_network_timeout()
    test_server_error_500()

    print("=" * 80)
    print("ALL TESTS COMPLETE")
    print("=" * 80)
    print()


if __name__ == "__main__":
    main()
