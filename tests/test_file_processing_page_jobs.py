import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.models.file_processing_page_jobs import FileProcessingPageJobs
from app.db.models.file_processing_runs import FileProcessingRuns
from app.db.models.file_processing_staging import FileProcessingStaging
from app.db.models.restaurant import Restaurant
from app.db.models.user import User
from app.db.queries.file_processing import get_processing_status
from app.workers import file_processing_tasks


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_user_and_restaurant(db: Session) -> tuple[User, Restaurant]:
    user = User(telegram_id=1, chat_id=1)
    db.add(user)
    db.flush()

    restaurant = Restaurant(
        name="Test Restaurant",
        restaurant_code="TEST1",
        owner_user_id=user.id,
        onboarding_status={},
    )
    db.add(restaurant)
    db.flush()
    return user, restaurant


def test_processing_status_aggregates_page_jobs() -> None:
    engine = _make_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user, restaurant = _seed_user_and_restaurant(db)
        run = FileProcessingRuns(
            restaurant_id=restaurant.id,
            user_id=user.id,
            file_id="test-file",
            filename="test.pdf",
            mime_type="application/pdf",
            processing_type="invoice",
            status="processing",
            pages_total=3,
            pages_processed=0,
            current_stage="page_jobs_enqueued",
            started_at=dt.datetime.now(dt.UTC),
        )
        db.add(run)
        db.flush()

        staging = FileProcessingStaging(
            restaurant_id=restaurant.id,
            user_id=user.id,
            run_id=run.id,
            processing_type="invoice",
            extracted_data_json={},
            product_alias_matches_json={},
            status="processing",
        )
        db.add(staging)

        db.add(
            FileProcessingPageJobs(run_id=run.id, page_index=1, status="completed")
        )
        db.add(
            FileProcessingPageJobs(run_id=run.id, page_index=2, status="failed_ocr")
        )
        db.add(
            FileProcessingPageJobs(run_id=run.id, page_index=3, status="pending")
        )
        db.commit()

        status = get_processing_status(run.id, db)
        assert status["pages_completed"] == 1
        assert status["pages_failed"] == 1
        assert status["pages_processing"] == 1
        assert status["pages_total"] == 3
        assert status["progress_percentage"] == 33.3


def test_maybe_finalize_run_locks_once(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _make_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user, restaurant = _seed_user_and_restaurant(db)
        run = FileProcessingRuns(
            restaurant_id=restaurant.id,
            user_id=user.id,
            file_id="test-file",
            filename="test.pdf",
            mime_type="application/pdf",
            processing_type="invoice",
            status="processing",
            pages_total=2,
            pages_processed=0,
            current_stage=None,
            started_at=dt.datetime.now(dt.UTC),
        )
        db.add(run)
        db.flush()

        staging = FileProcessingStaging(
            restaurant_id=restaurant.id,
            user_id=user.id,
            run_id=run.id,
            processing_type="invoice",
            extracted_data_json={},
            product_alias_matches_json={},
            status="processing",
        )
        db.add(staging)

        db.add(
            FileProcessingPageJobs(run_id=run.id, page_index=1, status="completed")
        )
        db.add(
            FileProcessingPageJobs(run_id=run.id, page_index=2, status="failed_extraction")
        )
        db.commit()

        called: dict[str, tuple] = {}

        def _fake_apply_async(*args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        monkeypatch.setattr(
            file_processing_tasks.finalize_run_task,
            "apply_async",
            _fake_apply_async,
        )

        file_processing_tasks.maybe_finalize_run(run.id, db)
        db.refresh(run)

        assert run.current_stage == "merge_pending"
        assert "args" in called
