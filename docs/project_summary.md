# Resto Pilot Backend - Project Summary

Resto Pilot is an AI-powered assistant designed for restaurant management and operations. This repository contains the backend service, built as a FastAPI application designed to be deployed on AWS Lambda.

## Core Technology Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python)
- **Deployment**: [AWS Lambda](https://aws.amazon.com/lambda/) with [Mangum](https://mangum.io/) adapter
- **Database**: [PostgreSQL](https://www.postgresql.org/) (hosted on [Neon](https://neon.tech/))
- **ORM**: [SQLAlchemy](https://www.sqlalchemy.org/) with [Alembic](https://alembic.sqlalchemy.org/) for migrations
- **Integrations**: [Telegram Bot API](https://core.telegram.org/bots/api) for user interactions
- **Workers**: [Celery](https://docs.celeryq.dev/) for background processing (requires a running worker service)
- **Package Management**: [uv](https://github.com/astral-sh/uv)

## Project Structure & Architecture

The codebase follows a modular structure within the `app` directory:

- `app/api/`: REST API endpoints and routing.
- `app/ai/`: Agent loop, tool definitions, and vision client.
- `app/core/`: Centralized configuration, logging, and security.
- `app/db/`: Database models, schemas, and session management.
- `app/domain/`: Core business logic and services (e.g., restaurant management, user handling).
- `app/processing/`: File processing pipeline (invoice, price list, inventory photo extraction).
- `app/telegram/`: Logic for processing Telegram webhooks and AI interactions.
- `app/workers/`: Celery task entry points and worker DB helpers.

See also:
- Codebase summary: `docs/resto-pilot-codebase-summary.md`
- `app/schemas/`: Pydantic models for data validation and API responses.
- `app/handler.py`: Entry point for AWS Lambda deployment.

## Key Features

- **Telegram Bot Interface**: Users can interact with the Resto Pilot assistant via Telegram.
- **Tool-first Agent**: The LLM acts as a tool caller; tools enforce policies and permissions.
- **Restaurant Management**: Restaurant data, memberships, and invite codes.
- **Operations Schema**: Products, suppliers, invoices, inventory, and disputes.
- **File Processing**: Vision-based extraction for invoices/price lists/inventory photos with staging + review.
- **Scalable Serverless Infrastructure**: Designed for cost-effective scaling on AWS Lambda.
- **Robust Database Schema**: User + restaurant core plus operational tables and staging.
- **Memory as Context**: Conversation memory aids continuity but never blocks tool calls.

Note: even if the HTTP API is deployed serverless (e.g. Lambda), Celery tasks require a separate worker process/service to consume queued work.

## Getting Started (Quick Links)

- **Development**: See `README.md` for local setup and environment configuration.
- **Migrations**: Use `alembic` for database schema updates.
- **Deployment**: Refer to `README.md` for packaging and AWS deployment steps.
