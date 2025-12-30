# AWS SQS + Celery notes

This project supports using **AWS SQS** as the Celery broker. This note captures what changes relative to Redis and the reliability/cost concepts to keep in mind.

## Mental model: broker vs worker
- **Broker (SQS/Redis/RabbitMQ)** stores *queued* tasks until a worker consumes them.
- **Workers** pull tasks, run them, then **ack** (confirm completion).
- Most brokers (including SQS) are **at-least-once delivery**: duplicates are possible and must be tolerated.

## Visibility timeout (SQS)
**Visibility timeout** is the “lease” period for an SQS message:
- A worker receives a message → SQS hides it from other consumers for `visibility_timeout`.
- If the worker **deletes** the message within that window → done.
- If the worker **does not delete** it (crash/hang/network split/slow) → the message becomes visible again and another worker can receive it (duplicate processing).

### Why it matters for Celery
Celery + SQS ends up with two layers of retry/redelivery:
- **SQS redelivery** when a message is not deleted before visibility timeout.
- **Celery retry** when task code explicitly calls `retry(...)` (publishes additional messages).

### Ack semantics (high level)
- **Ack late** (delete only after task completes): safer if workers crash mid-task, but requires visibility timeout > worst-case runtime, or you’ll get duplicates while the first worker is still running.
- **Ack early** (delete on receipt): fewer duplicates, but a crash mid-task can lose work.

Rule of thumb for this repo (long-ish tasks, correctness matters):
- Prefer **ack late + idempotent processing + DLQ**.
- Set visibility timeout to **> p99 task runtime + buffer** (often 2× p99).
  - If tasks can be ~10–15 minutes, a visibility timeout of **30–45 minutes** is a common starting point.

Defaults configured in `app/workers/celery_app.py`:
- `task_acks_late=True`
- `task_reject_on_worker_lost=True`
- `worker_prefetch_multiplier=1`

## DLQ (dead-letter queue)
Configure a DLQ with a `maxReceiveCount` so poison messages don’t loop forever:
- Example: after 5 failed receives, SQS moves the message to the DLQ.
- You can alert on DLQ depth and inspect payloads.

## Duplicates are normal: design for idempotency
Even with a perfect setup, duplicates can happen (at-least-once).
Patterns that help:
- Use DB uniqueness/idempotency keys (this repo already has `telegram_messages.update_id` unique).
- Make `process_session(session_id)` safe to run more than once:
  - Check current `telegram_sessions.status` or record “already processed” in DB before doing expensive work.
  - Persist processing steps in `processing_events` so you can see what happened.

## Cost intuition: Redis “commands” vs SQS “requests”
Upstash charges by “commands” (reads/writes), and Celery+Redis can generate many commands per task.
SQS charges per API request and can be cheaper/more predictable for queueing at scale.

## QStash vs SQS (why QStash is not a drop-in Celery broker)
- **QStash** is HTTP push delivery (“call my endpoint later”), not a worker-pulled message broker.
- Using QStash usually means you’d restructure to “QStash → HTTP endpoint → run work”, rather than “Celery worker consumes from broker”.
- You *can* build a QStash-driven system, but it’s a different architecture than Celery’s broker/worker loop.

## Kombu logs
Seeing “kombu” output is expected:
- **Kombu is Celery’s messaging layer** and is used for Redis, SQS, RabbitMQ, etc.
- It doesn’t imply the broker itself is “using kombu”; it’s just Celery internals.

## Practical recommendations (AWS)
- Start with **SQS Standard** unless you require strict ordering (FIFO).
- Configure:
  - Visibility timeout (based on p95/p99 runtime).
  - DLQ + maxReceiveCount.
  - Long polling (reduces empty receives).
- Add runtime measurements (e.g., write `processing_events` for start/end timestamps) to compute p95/p99 and tune settings confidently.
