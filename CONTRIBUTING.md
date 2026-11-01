# Contributing

Thanks for helping out. Radar is two projects in one repository, and changes to either go through
the same pull-request flow.

## Setup

- Backend: Python 3.12 and [uv](https://docs.astral.sh/uv/). See [backend/README.md](backend/README.md).
- Desktop: Node.js 20+, npm and stable Rust. See [desktop/README.md](desktop/README.md).

## Before you open a pull request

Run the same checks CI runs:

```bash
cd backend
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest

cd ../desktop
npm ci
npm run build
cd src-tauri
cargo check
```

Tests marked `live` call Google Calendar and are skipped unless `RADAR_LIVE_TESTS=1` is set.
CI must not depend on them.

## Guidelines

- Keep each pull request to one change, and say how you tested it.
- A new collector needs a fixture under `backend/tests/fixtures/` and a test. Tests must not hit the
  network.
- Schema changes need an Alembic migration (`uv run alembic revision --autogenerate -m "..."`).
- Never commit `.env`, `token.json`, `credentials.json`, `client_secret*.json`, `*.db` or
  `settings.json`. If a secret leaks, follow [SECURITY.md](SECURITY.md).
