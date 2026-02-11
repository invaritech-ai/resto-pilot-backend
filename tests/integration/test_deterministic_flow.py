"""
Integration tests for deterministic flow via test API.

These tests verify the end-to-end flow:
  user message → planner → validation → execution → presenter → response

Run with: pytest tests/integration/test_deterministic_flow.py -v
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.models.base import Base
from app.db.models.user import User
from app.main import create_app


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Get test settings."""
    settings = get_settings()
    # Ensure we're using test database
    assert "test" in settings.database_url.lower() or "dev" in settings.database_url.lower(), \
        "Must use test/dev database for integration tests"
    return settings


@pytest.fixture(scope="module")
def test_client(settings: Settings) -> TestClient:
    """Create test client."""
    app = create_app(settings=settings)
    return TestClient(app)


@pytest.fixture(scope="module")
def db_session(settings: Settings):
    """Create database session for tests."""
    engine = create_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="module")
def test_user(db_session: Session) -> User:
    """Get or create a test user."""
    # Try to find existing test user
    user = db_session.scalar(
        select(User).where(User.telegram_id == 999999999)
    )
    if user:
        return user

    # Create test user if doesn't exist
    user = User(
        telegram_id=999999999,
        chat_id=999999999,
        full_name="Test User",
        username="testuser",
        phone="+1234567890",
        is_phone_verified=True,
        state="IDLE",
        state_data={},
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture(scope="module")
def secret_token(settings: Settings) -> str:
    """Get side-channel secret token."""
    token = settings.side_channel_secret_token
    if not token:
        pytest.skip("SIDE_CHANNEL_SECRET_TOKEN not configured")
    return token


class TestDeterministicFlowBasic:
    """Basic deterministic flow tests."""

    def test_profile_get(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test profile get via deterministic flow."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200, f"Failed: {response.text}"

        data = response.json()
        assert data["user_id"] == str(test_user.id)
        assert data["telegram_id"] == test_user.telegram_id

        # Check planner decision
        assert data["planner_decision"]["action"] == "call_tool"
        assert data["planner_decision"]["tool"] == "profile_get"

        # Check validation
        assert data["validation_passed"] is True
        assert data["validation_errors"] == []

        # Check execution
        assert data["tool_executed"] is True
        assert data["tool_error"] is None
        assert data["tool_result"] is not None

        # Check response
        assert len(data["response_text"]) > 0
        assert test_user.full_name in data["response_text"]

    def test_profile_update(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test profile update via deterministic flow."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "update my name to Integration Test User",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200, f"Failed: {response.text}"

        data = response.json()

        # Check planner decision
        assert data["planner_decision"]["action"] == "call_tool"
        assert data["planner_decision"]["tool"] == "profile_update"
        assert "full_name" in data["planner_decision"]["args"]
        assert "Integration Test User" in data["planner_decision"]["args"]["full_name"]

        # Check validation
        assert data["validation_passed"] is True

        # Check execution
        assert data["tool_executed"] is True
        assert data["tool_error"] is None


class TestDeterministicFlowValidation:
    """Test validation layer."""

    def test_no_uuid_in_args(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test that planner doesn't leak UUIDs in args."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200

        data = response.json()
        args = data["planner_decision"].get("args", {})

        # Check no UUIDs in args
        import re
        uuid_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        args_str = str(args)
        assert not uuid_pattern.search(args_str), f"UUID found in args: {args}"


class TestDeterministicFlowTelemetry:
    """Test LLM telemetry tracking."""

    def test_planner_telemetry(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test planner telemetry is captured."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200

        data = response.json()

        # Check planner metrics
        assert data["planner_model"] is not None
        assert data["planner_latency_ms"] is not None
        assert data["planner_latency_ms"] > 0
        assert data["planner_tokens"] is not None
        assert data["planner_tokens"]["total_tokens"] > 0

    def test_presenter_telemetry(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test presenter telemetry is captured."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200

        data = response.json()

        # Check presenter metrics (only present when tool is executed)
        if data["tool_executed"]:
            assert data["presenter_model"] is not None
            assert data["presenter_latency_ms"] is not None
            assert data["presenter_latency_ms"] > 0
            assert data["presenter_tokens"] is not None
            assert data["presenter_tokens"]["total_tokens"] > 0


class TestDeterministicFlowSecurity:
    """Test security and authentication."""

    def test_requires_secret_token(self, test_client: TestClient, test_user: User):
        """Test that endpoint requires secret token."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            # No secret token header
        )
        assert response.status_code in [401, 403]

    def test_rejects_invalid_token(self, test_client: TestClient, test_user: User):
        """Test that endpoint rejects invalid token."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": "invalid-token"},
        )
        assert response.status_code == 403

    def test_requires_existing_user(self, test_client: TestClient, secret_token: str):
        """Test that endpoint requires existing user."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": 111111111,  # Non-existent user
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 404


class TestConsoleMode:
    """Test console mode functionality."""

    def test_console_mode_enabled(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test console mode captures output."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": True,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["chat_id"] == 0  # Console mode uses chat_id=0
        assert data["console_output"] is not None
        assert len(data["console_output"]) > 0
        assert "[BOT]" in data["console_output"][0]

    def test_console_mode_disabled(self, test_client: TestClient, test_user: User, secret_token: str):
        """Test console mode can be disabled."""
        response = test_client.post(
            "/api/v1/test/message",
            json={
                "telegram_id": test_user.telegram_id,
                "message": "show my profile",
                "console_mode": False,
            },
            headers={"X-Side-Channel-Secret-Token": secret_token},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["chat_id"] == test_user.chat_id  # Uses real chat_id
        assert data["console_output"] is None  # No console output
