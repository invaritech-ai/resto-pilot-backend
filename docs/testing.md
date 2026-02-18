# Testing Guide

## Test stack
- `pytest`
- unit + integration-style tests under `tests/`
- DB/session behavior tested with fixtures and mocks depending on module

## Run tests
```bash
# Full suite
uv run pytest

# Focused suites
uv run pytest tests/test_commands.py tests/test_buttons.py
uv run pytest tests/test_ocr_tasks.py tests/test_item_parser.py
uv run pytest tests/test_telegram_tasks.py tests/test_files_handler.py
```

## Current baseline
- Full suite: passing (see latest CI/local run output in PR/terminal logs)
- Keep full-suite green before pushing phase-completion commits

## Manual smoke checklist
1. Start API + worker.
2. Send `/start`, complete onboarding.
3. Run `/help` and verify all listed commands are usable.
4. Upload invoice and price-list sample files.
5. Validate review, edit, pagination, and confirm flows.
6. Validate `/products`, `/prices <supplier>`, `/inventory`, `/balance` outputs.
7. Validate stale pagination buttons are rejected after a newer list is sent.

## Regression areas to always include
- Supplier matching (`/prices`) with exact + partial names.
- Callback payload safety and handling of stale/invalid callback data.
- Inventory confirm path for duplicate item names and mixed resolutions.
- Telemetry side effects: outgoing message logs and LLM call usage rows.
