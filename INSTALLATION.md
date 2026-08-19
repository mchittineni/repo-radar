# Installation

Repo Radar is a small Python job: it asks the GitHub API which repositories an account owns, pulls
stats for each one, and rewrites `README.md`. You can run it locally, or let GitHub Actions run it
on a schedule.

- [Requirements](#requirements)
- [Local install](#local-install)
- [Getting a token](#getting-a-token)
- [First run](#first-run)
- [Running on GitHub Actions](#running-on-github-actions)
- [Configuration reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)

## Requirements

| | |
|---|---|
| Python | 3.11 or newer |
| Dependencies | `requests` (see `scripts/requirements.txt`) |
| GitHub token | classic PAT with `public_repo`, or a fine-grained token with read access |
| Optional | [`gh` CLI](https://cli.github.com/) to mint a token, `pre-commit` for hooks |

## Local install

```bash
git clone https://github.com/mchittineni/repo-radar.git
cd repo-radar

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r scripts/requirements.txt
```

## Getting a token

The GitHub API allows only 60 unauthenticated requests per hour, and each tracked repository costs
about four requests — so a token is effectively required.

**With the `gh` CLI:**

```bash
export GH_TOKEN=$(gh auth token)
```

**With a personal access token:** create one at
<https://github.com/settings/tokens>. A classic token needs `public_repo`; add `repo` only if you
want private repositories in the dashboard. Then either export it, or save it to a `.env` file that
`scripts/run.sh` loads automatically:

```bash
# .env  (git-ignored — never commit this)
GH_TOKEN=ghp_your_token_here
GH_USERNAME=mchittineni
```

## First run

```bash
# 1. Discover which repositories to track (writes data/repos.json)
python scripts/sync_repos.py --verbose

# 2. Rebuild the dashboard (rewrites README.md)
python scripts/update_status.py --sort stars --verbose
```

Or do both through the wrapper, from any directory:

```bash
./scripts/run.sh --sync --sort stars
```

Preview before writing anything:

```bash
python scripts/sync_repos.py --dry-run
```

## Running on GitHub Actions

The workflow at `.github/workflows/status-update.yml` runs every 12 hours and can be triggered
manually from the **Actions** tab.

1. **Push this repository to your own account.** The workflow commits back to the repository it
   runs in, so it needs to be yours:

   ```bash
   gh repo create <you>/repo-radar --source=. --remote=origin --public
   git push -u origin main
   ```

2. **Add the token as a secret** — *Settings → Secrets and variables → Actions → Secrets*:

   | Secret | Value |
   |---|---|
   | `GH_TOKEN` | a PAT as described above |

3. **Optionally set the tracked account** — *Settings → Secrets and variables → Actions →
   Variables*:

   | Variable | Default | Purpose |
   |---|---|---|
   | `GH_USERNAME` | `mchittineni` | the account whose repositories are tracked |

4. **Run it once by hand**: *Actions → Update Repository Status → Run workflow*. The job syncs
   `data/repos.json`, rebuilds `README.md`, and commits with
   `fix(git-tracker): auto-updated project status with latest changes [skip ci]`.

To change the schedule, edit the `cron` expression:

```yaml
on:
  schedule:
    - cron: "0 */12 * * *"   # every 12 hours
```

## Configuration reference

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `GH_TOKEN` | — | GitHub token used for all API calls |
| `GH_USERNAME` | `mchittineni` | account whose repositories are tracked |
| `CONFIG_FILE` | `data/repos.json` | tracked repository list |
| `README_FILE` | `README.md` | dashboard file to rewrite |
| `METADATA_FILE` | `data/repo-metadata.json` | machine-readable export |

`scripts/sync_repos.py` flags:

| Flag | Effect |
|---|---|
| `-u`, `--user` | discover repositories for a different account |
| `--include-forks` | keep forks (skipped by default) |
| `--include-archived` | keep archived repositories (skipped by default) |
| `--include-private` | keep private repositories (needs a token with `repo`) |
| `-n`, `--dry-run` | print the list without writing the config file |

`scripts/update_status.py` flags:

| Flag | Effect |
|---|---|
| `-s`, `--sort` | sort by `stars`, `forks`, `issues`, or `updated` |
| `-c`, `--config` | use a different repository list |
| `-r`, `--readme` | write to a different README |
| `-f`, `--force` | continue even when the rate limit is nearly exhausted |
| `-v`, `--verbose` | debug logging |

## Troubleshooting

**`GitHub token not found. Set the GH_TOKEN environment variable.`**
The script exits early without a token. Export `GH_TOKEN` or create a `.env` file.

**`GitHub API rate limit exceeded`**
Wait for the reset time in the log, or pass `--force` to push on with what is left. Each repository
costs roughly four API calls.

**`Repository not found: <name>`**
The entry in `data/repos.json` was renamed, deleted, or made private. Re-run
`python scripts/sync_repos.py` to refresh the list.

**No commit is created in Actions**
The job only commits when files actually changed, and it needs `contents: write` permission (already
set in the workflow).

**Only public repositories appear**
Private repositories need a token with `repo` scope *and*
`python scripts/sync_repos.py --include-private`.

**The dashboard looks wrong after hand-editing `README.md`**
Content inside the `START_REPO_STATUS` / `END_REPO_STATUS` markers is regenerated every run. Change
the generator in `scripts/update_status.py` instead.
