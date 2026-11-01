# Security policy

## Reporting a vulnerability or a leaked secret

Please **do not open a public issue** for a security problem, and never paste a secret (database
URL, API token, OAuth token, API key, webhook URL) into an issue, pull request or discussion.

Report it privately instead:

- Use **Security → Report a vulnerability** (private vulnerability reporting) on this repository, or
- Contact the maintainer, [@Berserker-GM](https://github.com/Berserker-GM), directly.

Say what you found, where (file, commit or URL) and how to reproduce it.

## If a secret was committed

Treat it as compromised even if the commit has been removed. Rotate it first (Neon password,
`API_TOKEN`, Google OAuth token, clist.by key, alert or digest webhook), then update the GitHub
Actions secrets and your local `.env`.

## Supported versions

Only the latest commit on `main` is supported.
