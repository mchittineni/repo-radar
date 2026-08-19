# Security Policy

## Scope

Repo Radar reads public metadata from the GitHub API and writes a Markdown dashboard. It holds no
user data and exposes no network service. The security surface is therefore small and specific:

- the GitHub token supplied through `GH_TOKEN`
- the GitHub Actions workflow, which has `contents: write` on this repository
- the third-party actions and Python dependencies the workflow installs

## Supported versions

The `main` branch is the only supported version. Fixes land there; there are no backports.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private reporting instead:

1. Go to the [Security tab](https://github.com/mchittineni/repo-radar/security).
2. Choose **Report a vulnerability** to open a private advisory.

If private advisories are unavailable, contact [@mchittineni](https://github.com/mchittineni)
directly and wait for a reply before disclosing anything publicly.

Please include:

- what the issue is and why it matters
- steps to reproduce, or a proof of concept
- the affected file, workflow, or dependency
- any suggested fix

**What to expect:** an acknowledgement within 7 days, an assessment within 14 days, and credit in
the advisory once a fix ships — unless you would rather stay anonymous.

## Token handling

The tracker needs the narrowest token that still works:

| Use case | Scope |
|---|---|
| Public repositories only (the default) | classic `public_repo`, or a fine-grained token with read-only `Metadata` |
| Including private repositories | classic `repo` (broad — only if you need it) |

Rules of thumb:

- Store tokens in GitHub Actions **secrets** or a local `.env` file. `.env` and `*.bak` are
  git-ignored; keep it that way.
- Never paste a token into `data/repos.json`, `README.md`, an issue, or a pull request.
- Rotate the token if it ever appears in a log, a screenshot, or a pushed commit.
- The workflow prints rate-limit output; that response contains no secrets, but avoid adding steps
  that echo `${{ secrets.GH_TOKEN }}`.

## Hardening already in place

- Third-party actions are pinned to full commit SHAs, so a moved tag cannot change what runs.
- The workflow grants only `contents: write` — no `packages`, `id-token`, or `pull-requests`.
- The scheduled commit uses `[skip ci]`, so it cannot trigger itself in a loop.
- `detect-private-key` and `detect-secrets` run as pre-commit hooks.
- Dependabot watches both `scripts/requirements.txt` and the workflow's actions.

## Out of scope

- Rate-limit exhaustion from running the tracker too often.
- Inaccurate dashboard data caused by a stale `data/repos.json`; re-run `scripts/sync_repos.py`.
- Vulnerabilities in GitHub itself — report those to
  [GitHub](https://bounty.github.com/).
