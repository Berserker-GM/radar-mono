# Radar

Radar is an opportunity and deadline radar. It scans contest, hackathon and college-notice sources,
scores what it finds, and puts the important items in a small system-tray app, with one-click
"Add to Google Calendar".

- **Backend** (`backend/`): Python 3.12, collectors and a scoring pipeline, SQLAlchemy/Alembic, a
  FastAPI feed API, and a CLI (`python -m radar.cli scan-all`) that a daily GitHub Action runs.
- **Desktop** (`desktop/`): a Tauri 2 + TypeScript tray app that polls the API, shows the feed in a
  popup and sends desktop notifications for new important items.

## How it fits together

```text
 Sources                         Pipeline                    Storage
 -------                         --------                    -------
 clist.by ---------+
 Codeforces -------+
 Devfolio ---------+        +--------------------+      +--------------------+
 Devpost ----------+------> | collect -> ingest  | ---> | Neon Postgres      |
 Unstop -----------+        | (dedupe, rule-     |      |   (prod / CI)      |
 LeetCode ----------+       |  based extraction) |      | SQLite radar.db    |
 RSS feeds ---------+       |        -> score    |      |   (local dev)      |
 ICS calendars ----+         +--------------------+      +--------------------+
 College notices --+
          ^                                             +---------+----------+
          | daily-scan.yml (07:30 UTC)                            |
          | or "Scan now" from the tray                           v
                                                        +--------------------+
 Google Calendar <-- "Add to Calendar" ---------------- | FastAPI            |
 Calendar apps  <-- GET /feed.ics (token in URL) -----  | Bearer API_TOKEN   |
                                                        +---------+----------+
                                                                  | GET /feed, actions
                                                                  v
                                                        +--------------------+
                                                        | Tray app (Tauri)   |
                                                        | popup + notices    |
                                                        +--------------------+
```

Everything runs without any LLM or paid API. College notices are parsed with
deterministic rules (labeled lines, keyword roles, dateparser); contests,
hackathons and feeds come from structured APIs.

## Repository layout

```text
.
├── backend/              Python API, CLI, collectors, pipeline, Alembic migrations, tests
├── desktop/              Tauri 2 tray app (TypeScript frontend + src-tauri/ Rust shell)
└── .github/
    ├── workflows/
    │   ├── ci.yml          lint, tests and builds for both projects
    │   └── daily-scan.yml  scheduled scan-all against the hosted database
    ├── ISSUE_TEMPLATE/     bug and feature templates
    └── pull_request_template.md
```

- [backend/README.md](backend/README.md): running the API, environment variables, the daily Action
  and its secrets.
- [desktop/README.md](desktop/README.md): installing, running and building the tray app.

## Quick start

```bash
# API
cd backend
uv sync
cp .env.example .env          # set API_TOKEN at least
uv run alembic upgrade head
make run-api                  # http://127.0.0.1:8000

# Tray app, in a second terminal
cd desktop
npm ci
npm run tauri dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks to run before a pull request and
[SECURITY.md](SECURITY.md) for reporting problems privately.

## License

[MIT](LICENSE)
