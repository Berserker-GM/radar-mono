# Radar

Radar scans contest, hackathon and opportunity sources, scores the events, and serves a feed to the
desktop tray app ([`../desktop`](../desktop)). See [docs/SPEC.md](docs/SPEC.md) for the full design.

## Running the API

```bash
uv sync
cp .env.example .env        # then fill in the values below
uv run alembic upgrade head # create or upgrade the database schema
make run-api                # http://127.0.0.1:8000
```

Every endpoint needs `Authorization: Bearer <API_TOKEN>`. Use the same URL and token in the desktop
app's setup form. To scan from the command line instead: `uv run python -m radar.cli scan-all`.

Calendar apps cannot send bearer headers, so `GET /feed.ics?token=<API_TOKEN>` (no bearer header)
serves the saved, college and urgent slice of the feed as an iCalendar URL you can subscribe to from
Google Calendar ("From URL") or any other calendar app.

## Sources

The API sources (codeforces, clist, devpost, unstop, devfolio, leetcode) need no configuration.
The config-driven ones are skipped until their `sources` row has a `config`:

| Source | `config` JSON |
| --- | --- |
| `college` | `{"list_url": "https://...", "selectors": {"title": "a.notice-title", "link": "a.notice-title", "date": "time.notice-date"}}` |
| `rss` | `{"feeds": ["https://example/feed.xml", {"url": "...", "category": "college"}]}` |
| `ics` | `{"calendars": ["https://example.org/calendar.ics"]}` |

## Environment variables

Set these in `.env`. `.env.example` lists all of them.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. Defaults to `sqlite:///./radar.db`. Use Postgres (Neon, Supabase) for the GitHub Action. |
| `API_TOKEN` | Bearer token the API requires. If it's empty, every request gets 401. |
| `CLIST_USERNAME` | clist.by username for the contest source. |
| `CLIST_API_KEY` | clist.by API key. |
| `GOOGLE_TOKEN_PATH` | Path to the OAuth token file. Defaults to `token.json`. |
| `GOOGLE_TOKEN_JSON` | Contents of that token file, used instead of the path. Use this in CI. |
| `GOOGLE_CALENDAR_ID` | Calendar that events are added to. Defaults to `primary`. |
| `ALERT_WEBHOOK_URL` | Optional. A Discord webhook or Telegram `sendMessage` URL that is notified when a source fails or goes stale. |
| `DIGEST_WEBHOOK_URL` | Optional. A Discord webhook or Telegram `sendMessage` URL that receives a daily digest of the feed after each `scan-all`. |

To create `token.json`, download an OAuth client of type "Desktop app" from Google Cloud as
`credentials.json` in this folder, then run `uv run python scripts/google_calendar_auth.py` and approve
the browser prompt. Without a token, "Add to Calendar" returns 503.

## Daily GitHub Action

[.github/workflows/daily-scan.yml](../.github/workflows/daily-scan.yml) (at the repository root) runs
`python -m radar.cli scan-all` every day at 07:30 UTC (13:00 IST; GitHub can start it 15–30 minutes
late). It can also be started by hand from the Actions tab (**Run workflow**). The runner's disk is
wiped after each run, so the database has to be hosted Postgres. Run
`DATABASE_URL=<that url> uv run alembic upgrade head` once from your machine, because the workflow
doesn't migrate.

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Value |
| --- | --- |
| `DATABASE_URL` | The hosted Postgres URL (required). |
| `CLIST_USERNAME`, `CLIST_API_KEY` | Same as in `.env`. |
| `GOOGLE_TOKEN_JSON` | The full contents of `token.json`. |
| `GOOGLE_CALENDAR_ID` | Optional; defaults to `primary`. |
| `ALERT_WEBHOOK_URL` | Optional. A Discord webhook or Telegram `sendMessage` URL that is notified when a source fails or goes stale. |
| `DIGEST_WEBHOOK_URL` | Optional. Sends the daily digest right after the scan. |

The digest can also be sent on demand: `uv run python -m radar.cli digest` (exits non-zero when
`DIGEST_WEBHOOK_URL` is not set).

## Development

```bash
make lint   # ruff check + ruff format --check
make test   # pytest
```
