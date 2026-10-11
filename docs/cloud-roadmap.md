# Cloud Roadmap

The matching engine does not need an external AI API. It is deterministic Python
code and can run on a laptop, server, scheduled container, or behind a future API.

## Recommended first deployment

1. Package the scanner and Chromium dependencies in a container.
2. Run it on a schedule while the personal computer is off.
3. Store jobs on persistent storage; use managed PostgreSQL if the platform has an
   ephemeral filesystem, or SQLite on a single persistent VM.
4. Add Telegram or email notifications for newly discovered relevant jobs.
5. Keep resume parsing, matching, and tailoring local by default so resume contents
   are not uploaded to a third party.

## Deferred until needed

- Public web API and authentication
- Resume upload or cloud storage
- External LLM provider
- Multi-user accounts
- Queues or Kubernetes
- Automatic application submission

An LLM can later be an optional adapter for better wording. Deterministic matching
must remain available as the private, free, testable baseline.
