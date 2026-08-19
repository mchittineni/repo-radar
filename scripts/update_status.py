#!/usr/bin/env python3
"""
GitHub Repository Status Tracker

This script fetches status information for GitHub repositories and updates
a README.md file with the latest information.

Features:
- Fetches repository metadata from GitHub API
- Generates badges and status sections for each repository
- Updates README.md with the latest information
- Handles rate limiting and errors gracefully
- Provides configurable options through environment variables
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

# Paths: code lives in scripts/, generated state lives in data/
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(DATA_DIR / "repo_status.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("repo_status")

# Configuration
GH_USERNAME = os.getenv("GH_USERNAME", "mchittineni")
GH_TOKEN = os.getenv("GH_TOKEN")
CONFIG_FILE = os.getenv("CONFIG_FILE", str(DATA_DIR / "repos.json"))
README_FILE = os.getenv("README_FILE", str(REPO_ROOT / "README.md"))
METADATA_FILE = os.getenv("METADATA_FILE", str(DATA_DIR / "repo-metadata.json"))
DESCRIPTIONS_FILE = os.getenv("DESCRIPTIONS_FILE", str(DATA_DIR / "repo-descriptions.json"))
API_URL = "https://api.github.com/repos/{}/{}"
BADGE_TEMPLATE = "![Last Updated](https://img.shields.io/badge/Last%20Updated-{}-blue?style=flat-square)"

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
API_TIMEOUT = 10  # seconds
RATE_LIMIT_WAIT = 60  # seconds

class ApiRateLimitExceeded(Exception):
    """Exception raised when GitHub API rate limit is exceeded"""
    pass

def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Update GitHub repository status in README.md")
    parser.add_argument(
        "-c", "--config",
        help=f"Path to configuration file (default: {CONFIG_FILE})",
        default=CONFIG_FILE
    )
    parser.add_argument(
        "-r", "--readme",
        help=f"Path to README file (default: {README_FILE})",
        default=README_FILE
    )
    parser.add_argument(
        "-v", "--verbose",
        help="Enable verbose logging",
        action="store_true"
    )
    parser.add_argument(
        "-f", "--force",
        help="Force update even if rate limited",
        action="store_true"
    )
    parser.add_argument(
        "-s", "--sort",
        help="Sort repositories by field (stars, forks, issues, updated)",
        choices=["stars", "forks", "issues", "updated"],
        default=None
    )
    return parser.parse_args()

def load_repos(config_file: str) -> list[str]:
    """
    Load repository names from the configuration file.

    Args:
        config_file: Path to the configuration file

    Returns:
        List of repository names

    Raises:
        FileNotFoundError: If the config file doesn't exist
        json.JSONDecodeError: If the config file isn't valid JSON
    """
    try:
        config_path = Path(config_file)
        if not config_path.exists():
            logger.error(f"Configuration file not found: {config_file}")
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_path, encoding="utf-8") as file:
            data = json.load(file)

        if "repositories" not in data:
            logger.error("Invalid configuration file: 'repositories' key not found")
            raise KeyError("Invalid configuration file: 'repositories' key not found")

        # Remove any duplicate repository names while preserving order
        repos = []
        seen = set()
        for repo in data["repositories"]:
            if repo not in seen:
                repos.append(repo)
                seen.add(repo)

        logger.info(f"Loaded {len(repos)} repositories from config file")
        return repos

    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in configuration file: {config_file}")
        raise
    except Exception as e:
        logger.error(f"Error loading repositories: {e}")
        raise

def check_rate_limit(force: bool = False) -> bool:
    """
    Check GitHub API rate limit status.

    Args:
        force: If True, continue even if rate limited

    Returns:
        True if under rate limit, False otherwise

    Raises:
        ApiRateLimitExceeded: If rate limited and not forcing
    """
    try:
        response = requests.get(
            "https://api.github.com/rate_limit",
            auth=(GH_USERNAME, GH_TOKEN),
            timeout=API_TIMEOUT
        )
        response.raise_for_status()

        data = response.json()
        core = data["resources"]["core"]
        remaining = core["remaining"]
        reset_time = datetime.fromtimestamp(core["reset"])

        logger.info(f"API Rate Limit: {remaining}/{core['limit']} requests remaining, resets at {reset_time}")

        if remaining < 5 and not force:
            wait_time = core["reset"] - int(time.time()) + 5  # Add buffer
            logger.warning(f"Rate limit low: {remaining} requests remaining. Reset in {wait_time} seconds.")
            raise ApiRateLimitExceeded(f"GitHub API rate limit reached. Reset at {reset_time}")

        return remaining > 0

    except requests.RequestException as e:
        logger.warning(f"Could not check rate limit: {e}")
        return True  # Assume it's OK if we can't check

def fetch_repo_status(repo: str, retries: int = MAX_RETRIES) -> dict[str, Any] | None:
    """
    Fetch repository status from GitHub API.

    Args:
        repo: Repository name
        retries: Number of retry attempts for transient errors

    Returns:
        Dictionary containing repository status information or None if failed
    """
    url = API_URL.format(GH_USERNAME, repo)

    for attempt in range(retries):
        try:
            logger.info(f"Fetching data for {repo} (attempt {attempt+1})")
            response = requests.get(
                url,
                auth=(GH_USERNAME, GH_TOKEN),
                timeout=API_TIMEOUT,
                headers={"Accept": "application/vnd.github.v3+json"}
            )

            if response.status_code == 403 and "rate limit exceeded" in response.text.lower():
                logger.warning("Rate limit exceeded during repository fetch")
                raise ApiRateLimitExceeded("Rate limit exceeded during repository fetch")

            if response.status_code == 404:
                logger.warning(f"Repository not found: {repo}")
                return None

            response.raise_for_status()
            data = response.json()

            # Fetch latest commit data
            commits_url = f"https://api.github.com/repos/{GH_USERNAME}/{repo}/commits?per_page=1"
            commits_response = requests.get(
                commits_url,
                auth=(GH_USERNAME, GH_TOKEN),
                timeout=API_TIMEOUT
            )

            commit_sha = None
            if commits_response.status_code == 200:
                latest_commit_data = commits_response.json()
                if latest_commit_data:
                    commit_sha = latest_commit_data[0]["sha"]
                    commit_url = f"https://github.com/{GH_USERNAME}/{repo}/commit/{commit_sha}"
                    commit_date = latest_commit_data[0]["commit"]["author"]["date"].split("T")[0]
                    commit_author = latest_commit_data[0]["commit"]["author"]["name"]
                    commit_message = latest_commit_data[0]["commit"]["message"].split("\n")[0]
                else:
                    commit_url = f"https://github.com/{GH_USERNAME}/{repo}/commits"
                    commit_date = "N/A"
                    commit_author = "Unknown"
                    commit_message = "No commits found"
            else:
                commit_url = f"https://github.com/{GH_USERNAME}/{repo}/commits"
                commit_date = "N/A"
                commit_author = "Unknown"
                commit_message = "Could not fetch commit info"

            # Latest workflow run across all workflows (single API call)
            runs_url = f"https://api.github.com/repos/{GH_USERNAME}/{repo}/actions/runs?per_page=1"
            runs_response = requests.get(
                runs_url,
                auth=(GH_USERNAME, GH_TOKEN),
                timeout=API_TIMEOUT
            )

            ci_cd_status = "⚪"
            if runs_response.status_code == 200:
                runs_data = runs_response.json()
                if runs_data.get("total_count", 0) > 0:
                    status = runs_data["workflow_runs"][0]["conclusion"]
                    if status == "success":
                        ci_cd_status = "✅"
                    elif status == "failure":
                        ci_cd_status = "❌"
                    else:
                        ci_cd_status = "🔄"

            # Get repository languages
            languages_url = f"https://api.github.com/repos/{GH_USERNAME}/{repo}/languages"
            languages_response = requests.get(
                languages_url,
                auth=(GH_USERNAME, GH_TOKEN),
                timeout=API_TIMEOUT
            )

            languages = []
            language_bytes: dict[str, int] = {}
            if languages_response.status_code == 200:
                languages_data = languages_response.json()
                language_bytes = dict(
                    sorted(languages_data.items(), key=lambda item: item[1], reverse=True)
                )
                languages = list(language_bytes.keys())

            # Get license information
            license_info = data.get("license", {})
            license_name = license_info.get("name", "No license") if license_info else "No license"

            # Parse the date properly
            updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
            formatted_date = updated_at.strftime("%Y-%m-%d")

            return {
                "name": repo,
                "url": data["html_url"],
                "description": data.get("description", "No description provided"),
                "last_updated": formatted_date,
                "latest_commit": commit_url,
                "commit_sha": commit_sha,
                "commit_date": commit_date,
                "commit_message": commit_message,
                "author": commit_author,
                "issues": data["open_issues_count"],
                "stars": data["stargazers_count"],
                "forks": data["forks_count"],
                "watchers": data["watchers_count"],
                "ci_cd_status": ci_cd_status,
                "languages": languages[:5],  # Limit to top 5 languages
                "language_bytes": language_bytes,
                "license": license_name,
                "topics": data.get("topics", []),
                "raw_updated_at": data["updated_at"]  # Keep raw date for sorting
            }

        except ApiRateLimitExceeded:
            logger.warning(f"Rate limit exceeded, waiting {RATE_LIMIT_WAIT} seconds")
            time.sleep(RATE_LIMIT_WAIT)
            if attempt == retries - 1:
                raise

        except requests.RequestException as e:
            logger.warning(f"Error fetching {repo} (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                delay = RETRY_DELAY * (attempt + 1)
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                logger.error(f"Failed to fetch data for {repo} after {retries} attempts")
                return None

        except Exception as e:
            logger.error(f"Unexpected error for {repo}: {e}")
            return None

    return None

def load_descriptions(path: str = DESCRIPTIONS_FILE) -> dict[str, dict[str, Any]]:
    """
    Load the optional hand-written blurbs that override GitHub metadata.

    Args:
        path: Path to the descriptions file

    Returns:
        Mapping of repository name to its curated fields, empty when unavailable
    """
    try:
        descriptions_path = Path(path)
        if not descriptions_path.exists():
            logger.info(f"No curated descriptions found at {path}")
            return {}

        with open(descriptions_path, encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            logger.warning(f"Ignoring {path}: expected an object keyed by repository name")
            return {}

        logger.info(f"Loaded curated descriptions for {len(data)} repositories")
        return data

    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read {path}: {e}")
        return {}

DESCRIPTIONS: dict[str, dict[str, Any]] = load_descriptions()

def normalize_description(text: str | None, max_len: int = 140) -> str:
    """Flatten styled Unicode and trim descriptions for readable README text."""
    if not text or not text.strip():
        return "No description provided."

    cleaned = unicodedata.normalize("NFKC", text)
    cleaned = re.sub(r"[\U0001D400-\U0001D7FF\U0001D600-\U0001D64F]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) > max_len:
        clipped = cleaned[: max_len - 1]
        boundary = clipped.rfind(" ")
        if boundary > max_len // 2:
            clipped = clipped[:boundary]
        cleaned = clipped.rstrip(" ,;:.") + "…"

    return cleaned or "No description provided."

def format_number(value: int | float) -> str:
    """Format integers with thousands separators for tables."""
    return f"{int(value):,}"

LANGUAGE_EMOJI = {
    "Python": "🐍",
    "JavaScript": "🟨",
    "TypeScript": "🔷",
    "Go": "🐹",
    "Rust": "🦀",
    "Java": "☕",
    "C": "🇨",
    "C++": "➕",
    "C#": "🎯",
    "Ruby": "💎",
    "Shell": "🐚",
    "PowerShell": "💠",
    "HCL": "🏗️",
    "Dockerfile": "🐳",
    "HTML": "🌐",
    "CSS": "🎨",
    "SCSS": "🎨",
    "Vue": "💚",
    "Svelte": "🧡",
    "Makefile": "🔨",
    "Jupyter Notebook": "📓",
    "Smarty": "🧩",
    "Mustache": "🥸",
    "Jinja": "🧪",
    "Nix": "❄️",
    "Lua": "🌙",
    "Kotlin": "🟪",
    "Swift": "🕊️",
    "PHP": "🐘",
    "SQL": "🗄️",
    "PLpgSQL": "🐘",
    "Terraform": "🏗️",
}

CI_LABELS = {
    "✅": "✅ pass",
    "❌": "❌ fail",
    "🔄": "🔄 running",
    "⚪": "· none",
}

MEDALS = ["🥇", "🥈", "🥉"]

def language_emoji(language: str) -> str:
    """Pick a playful icon for a language, falling back to a generic one."""
    return LANGUAGE_EMOJI.get(language, "🧠")

def format_ci_label(status: str) -> str:
    """Map CI status emoji to a compact table label."""
    return CI_LABELS.get(status, "· none")

def truncate_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."

def relative_age(raw_timestamp: str | None) -> str:
    """Render an ISO timestamp as a friendly age such as "3 days ago"."""
    if not raw_timestamp:
        return "unknown"

    try:
        moment = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"

    delta = datetime.now(UTC) - moment
    days = delta.days

    if days <= 0:
        hours = delta.seconds // 3600
        if hours <= 1:
            return "just now"
        return f"{hours} hours ago"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months > 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years > 1 else ''} ago"

def heat_marker(raw_timestamp: str | None) -> str:
    """Signal how recently a repository moved."""
    if not raw_timestamp:
        return "💤"

    try:
        moment = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return "💤"

    days = (datetime.now(UTC) - moment).days
    if days <= 2:
        return "🔥"
    if days <= 14:
        return "✨"
    if days <= 90:
        return "🌱"
    return "💤"

def spark_bar(value: int, peak: int, width: int = 18) -> str:
    """Draw a proportional block bar for a value against the largest value."""
    if peak <= 0:
        return "░" * width
    filled = max(1, round(width * value / peak)) if value > 0 else 0
    return "█" * filled + "░" * (width - filled)

def generate_stat_cards(repos_data: list[dict[str, Any]]) -> str:
    """Build the headline counters shown at the top of the generated section."""
    total_stars = sum(repo.get("stars", 0) for repo in repos_data)
    total_forks = sum(repo.get("forks", 0) for repo in repos_data)
    total_issues = sum(repo.get("issues", 0) for repo in repos_data)
    languages = {lang for repo in repos_data for lang in repo.get("language_bytes", {})}
    passing = sum(1 for repo in repos_data if repo.get("ci_cd_status") == "✅")
    hot = sum(1 for repo in repos_data if heat_marker(repo.get("raw_updated_at")) == "🔥")

    return "\n".join([
        "| 📦 Repos | ⭐ Stars | 🍴 Forks | 🐛 Open issues | 🧠 Languages | ✅ CI green | 🔥 Active now |",
        "|:--:|:--:|:--:|:--:|:--:|:--:|:--:|",
        f"| **{len(repos_data)}** | **{format_number(total_stars)}** | **{format_number(total_forks)}** | **{format_number(total_issues)}** | **{len(languages)}** | **{passing}/{len(repos_data)}** | **{hot}** |",
    ])

def language_totals(repos_data: list[dict[str, Any]]) -> dict[str, int]:
    """Sum bytes written per language across every tracked repository."""
    totals: dict[str, int] = {}
    for repo in repos_data:
        for language, size in repo.get("language_bytes", {}).items():
            totals[language] = totals.get(language, 0) + int(size)
    return dict(sorted(totals.items(), key=lambda item: item[1], reverse=True))

def format_bytes(size: int) -> str:
    """Render a byte count compactly (12.3 KB, 4.1 MB)."""
    step = 1024.0
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            precision = 0 if unit == "B" else 1
            return f"{value:.{precision}f} {unit}"
        value /= step
    return f"{value:.1f} GB"

def generate_language_mix(repos_data: list[dict[str, Any]]) -> str:
    """
    Render the language mix as expandable rows.

    Each language shows its share of the codebase as a bar and opens to reveal every
    repository that uses it, so the chart doubles as a directory.
    """
    totals = language_totals(repos_data)
    if not totals:
        return "_No language data available yet._"

    grand_total = sum(totals.values())
    peak = max(totals.values())
    drawers = []

    for language, size in totals.items():
        share = size / grand_total * 100 if grand_total else 0.0
        users = [repo for repo in repos_data if language in repo.get("language_bytes", {})]
        users.sort(key=lambda repo: repo.get("language_bytes", {}).get(language, 0), reverse=True)

        rows = "\n".join(
            "| {heat} [{name}]({url}) | {size} | ⭐ {stars} | {age} |".format(
                heat=heat_marker(repo.get("raw_updated_at")),
                name=repo["name"],
                url=repo["url"],
                size=format_bytes(repo.get("language_bytes", {}).get(language, 0)),
                stars=format_number(repo.get("stars", 0)),
                age=relative_age(repo.get("raw_updated_at")),
            )
            for repo in users
        )

        drawers.append(
            "<details>\n"
            f"<summary>{language_emoji(language)} <strong>{language}</strong> "
            f"<code>{spark_bar(size, peak, width=22)}</code> "
            f"{share:.1f}% · {len(users)} repo{'s' if len(users) != 1 else ''}</summary>\n\n"
            "| Repository | Code | Stars | Updated |\n"
            "|------------|-----:|------:|---------|\n"
            f"{rows}\n\n</details>"
        )

    footer = (
        f"<sub>{format_bytes(grand_total)} of code across {len(totals)} languages · "
        "bars show each language's share of total bytes.</sub>"
    )
    return "\n\n".join([*drawers, footer])

def generate_leaderboard(repos_data: list[dict[str, Any]], top_n: int = 5) -> str:
    """Show the most starred repositories as a proportional bar chart."""
    ranked = sorted(repos_data, key=lambda repo: repo.get("stars", 0), reverse=True)[:top_n]
    if not ranked:
        return "_Nothing to rank yet._"

    peak = max(repo.get("stars", 0) for repo in ranked)
    rows = []
    for index, repo in enumerate(ranked):
        badge = MEDALS[index] if index < len(MEDALS) else f"{index + 1}."
        rows.append(
            "| {badge} | [{name}]({url}) | `{bar}` | **{stars}** |".format(
                badge=badge,
                name=repo["name"],
                url=repo["url"],
                bar=spark_bar(repo.get("stars", 0), peak),
                stars=format_number(repo.get("stars", 0)),
            )
        )

    return "\n".join([
        "| | Repository | Stars | |",
        "|:--:|------------|:------|--:|",
        *rows,
    ])

def generate_activity_feed(repos_data: list[dict[str, Any]], top_n: int = 6) -> str:
    """List the repositories that moved most recently, newest first."""
    ranked = sorted(
        repos_data,
        key=lambda repo: repo.get("raw_updated_at", ""),
        reverse=True
    )[:top_n]

    if not ranked:
        return "_No recent activity._"

    lines = []
    for repo in ranked:
        lines.append(
            "- {heat} **[{name}]({url})** · {age} · [{message}]({commit})".format(
                heat=heat_marker(repo.get("raw_updated_at")),
                name=repo["name"],
                url=repo["url"],
                age=relative_age(repo.get("raw_updated_at")),
                message=truncate_text(repo.get("commit_message", "No commits found"), 60),
                commit=repo["latest_commit"],
            )
        )
    return "\n".join(lines)

def generate_summary_table(repos_data: list[dict[str, Any]]) -> str:
    """Generate a compact overview table for all repositories."""
    rows = []
    for index, repo in enumerate(repos_data, start=1):
        rank = MEDALS[index - 1] if index <= len(MEDALS) else str(index)
        rows.append(
            "| {rank} | {heat} [{name}]({url}) | {stars} | {forks} | {issues} | {ci} | {age} |".format(
                rank=rank,
                heat=heat_marker(repo.get("raw_updated_at")),
                name=repo["name"],
                url=repo["url"],
                stars=format_number(repo.get("stars", 0)),
                forks=format_number(repo.get("forks", 0)),
                issues=format_number(repo.get("issues", 0)),
                ci=format_ci_label(repo.get("ci_cd_status", "⚪")),
                age=relative_age(repo.get("raw_updated_at")),
            )
        )

    return "\n".join([
        "| # | Repository | ⭐ | 🍴 | 🐛 | CI | Updated |",
        "|:--:|------------|------:|------:|-------:|:--:|---------|",
        *rows,
    ])

def generate_repo_details(repo: dict[str, Any]) -> str:
    """Generate a collapsible detail block for one repository."""
    name = repo["name"]
    curated = DESCRIPTIONS.get(name, {})
    title = curated.get("displayName") or name
    status = curated.get("status")
    tech_stack = curated.get("techStack") or []
    curated_description = curated.get("description")
    description = normalize_description(
        curated_description or repo.get("description"),
        max_len=200 if curated_description else 140,
    )
    commit_message = truncate_text(repo.get("commit_message", "No commits found"), 90)
    languages = repo.get("languages", [])
    topics = repo.get("topics", [])
    license_name = repo.get("license", "No license")
    lang_line = " · ".join(
        f"{language_emoji(lang)} `{lang}`" for lang in languages[:5]
    ) if languages else "_None detected_"
    topic_line = " · ".join(f"`{topic}`" for topic in topics[:6]) if topics else "_None_"

    status_chip = f" &nbsp;·&nbsp; <code>{status}</code>" if status else ""
    stack_line = " · ".join(f"`{item}`" for item in tech_stack[:6]) if tech_stack else None
    stack_row = f"\n| **Tech stack** | {stack_line} |" if stack_line else ""

    return f"""<!-- repo:{name} -->
<details>
<summary>
  {heat_marker(repo.get('raw_updated_at'))}
  <strong><a href="{repo['url']}">{title}</a></strong>
  &nbsp;<sub><code>{name}</code></sub>{status_chip}
  &nbsp;·&nbsp; ⭐ {format_number(repo.get('stars', 0))}
  &nbsp;·&nbsp; 🍴 {format_number(repo.get('forks', 0))}
  &nbsp;·&nbsp; CI {format_ci_label(repo.get('ci_cd_status', '⚪'))}
  <br><sub>{description}</sub>
</summary>
<br>

![Stars](https://img.shields.io/github/stars/{GH_USERNAME}/{name}?style=flat-square)
![Forks](https://img.shields.io/github/forks/{GH_USERNAME}/{name}?style=flat-square)
![Issues](https://img.shields.io/github/issues/{GH_USERNAME}/{name}?style=flat-square)
![Last Commit](https://img.shields.io/github/last-commit/{GH_USERNAME}/{name}?style=flat-square)

| | |
|---|---|
| **Latest commit** | [{commit_message}]({repo['latest_commit']}) |
| **Commit date** | `{repo.get('commit_date', 'Unknown')}` |
| **Author** | `{repo.get('author', 'Unknown')}` |
| **Repo updated** | `{repo.get('last_updated', 'Unknown')}` ({relative_age(repo.get('raw_updated_at'))}) |
| **License** | `{license_name}` |
| **Languages** | {lang_line} |
| **Topics** | {topic_line} |{stack_row}

<a href="{repo['url']}">Open repository →</a>

</details>"""

def generate_playbook() -> str:
    """Explain how to run the tracker, tucked inside a drawer."""
    return f"""<details>
<summary>🛠️ <strong>Run this tracker yourself</strong></summary>

```bash
pip install -r scripts/requirements.txt
export GH_TOKEN=<a token with public_repo scope>

# discover every repository owned by the tracked account
python scripts/sync_repos.py --verbose

# refresh this dashboard
python scripts/update_status.py --sort stars
```

Handy switches:

- `./scripts/run.sh --sync --sort stars` — sync the list, then rebuild the dashboard
- `python scripts/sync_repos.py --include-forks --include-private` — widen what gets tracked
- `python scripts/sync_repos.py --dry-run` — preview the repository list without writing it
- `GH_USERNAME=someone-else python scripts/update_status.py` — point the tracker at another account

Tracked account: **@{GH_USERNAME}** · list lives in `data/repos.json` · machine-readable export in `data/repo-metadata.json`.

</details>"""

def generate_repo_section(repos_data: list[dict[str, Any]]) -> str:
    """Generate the full auto-updated README body."""
    if not repos_data:
        return "No repository data available."

    nav = (
        "**Jump to** · [📊 Stats](#-at-a-glance) · [🏆 Leaderboard](#-star-leaderboard) · "
        "[⏱️ Activity](#-freshly-pushed) · [🧬 Languages](#-language-mix) · "
        "[📚 All repos](#-every-repository)"
    )

    return "\n\n".join([
        nav,
        "## 📊 At a glance",
        generate_stat_cards(repos_data),
        "## 🏆 Star leaderboard",
        generate_leaderboard(repos_data),
        "## ⏱️ Freshly pushed",
        generate_activity_feed(repos_data),
        "## 🧬 Language mix",
        "<sub>Open a language to see every repository that uses it, largest first.</sub>",
        generate_language_mix(repos_data),
        "## 📚 Every repository",
        generate_summary_table(repos_data),
        "<sub>Click any repository below to expand commit info, languages, and topics.</sub>",
        "\n\n".join(generate_repo_details(repo) for repo in repos_data),
        "---",
        generate_playbook(),
    ])

def build_readme_header(badge: str, repo_count: int, total_stars: int) -> str:
    """Build the static README intro shown above the generated section."""
    return f"""<div align="center">

# 🛰️ Repo Radar

**A living status board for [@{GH_USERNAME}](https://github.com/{GH_USERNAME})'s repositories.**

Every repository, star, commit, and CI result — refreshed automatically by GitHub Actions. 🤖

{badge}
![Repositories](https://img.shields.io/badge/Repositories-{repo_count}-6f42c1?style=flat-square)
![Stars](https://img.shields.io/badge/Stars-{format_number(total_stars).replace(",", "%2C")}-f1c40f?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-2ea44f?style=flat-square)

[Installation](INSTALLATION.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of Conduct](CODE_OF_CONDUCT.md)

</div>

---"""


def inject_into_readme(
    badge: str,
    new_content: str,
    readme_path: str,
    repo_count: int = 0,
    total_stars: int = 0,
) -> bool:
    """
    Update README.md with repository status information.

    Args:
        badge: Updated badge with timestamp
        new_content: Repository status content in markdown
        readme_path: Path to the README file
        repo_count: Number of repositories in the generated section
        total_stars: Combined star count for the header summary

    Returns:
        True if successful, False otherwise
    """
    try:
        readme_file = Path(readme_path)
        start_marker = "<!-- START_REPO_STATUS -->"
        end_marker = "<!-- END_REPO_STATUS -->"
        header = build_readme_header(badge, repo_count, total_stars)

        if not readme_file.exists():
            logger.warning(f"README file not found: {readme_path}")
            logger.info("Creating new README file")
            updated = f"{header}\n\n{start_marker}\n{new_content}\n{end_marker}\n"
            with open(readme_file, "w", encoding="utf-8") as file:
                file.write(updated)
            logger.info(f"✅ {readme_path} created with latest repo statuses!")
            return True

        with open(readme_file, encoding="utf-8") as file:
            readme = file.read()

        if start_marker in readme and end_marker in readme:
            after_marker = readme.split(end_marker, 1)[1]
            updated = f"{header}\n\n{start_marker}\n{new_content}\n{end_marker}{after_marker}"
        else:
            updated = f"{header}\n\n{start_marker}\n{new_content}\n{end_marker}\n"

        with open(readme_file, "w", encoding="utf-8") as file:
            file.write(updated)

        logger.info(f"✅ {readme_path} updated with latest repo statuses!")
        return True

    except Exception as e:
        logger.error(f"Error updating README: {e}")
        return False

def sort_repos(repos_data: list[dict[str, Any]], sort_by: str | None = None) -> list[dict[str, Any]]:
    """
    Sort repositories by the specified field.

    Args:
        repos_data: List of repository status dictionaries
        sort_by: Field to sort by (stars, forks, issues, updated)

    Returns:
        Sorted list of repository status dictionaries
    """
    if not sort_by or not repos_data:
        return repos_data

    # First, remove any duplicates by repo name
    unique_repos = {}
    for repo in repos_data:
        name = repo.get("name")
        if name not in unique_repos or (unique_repos[name].get("raw_updated_at", "") < repo.get("raw_updated_at", "")):
            unique_repos[name] = repo

    deduplicated_data = list(unique_repos.values())

    if sort_by == "stars":
        return sorted(deduplicated_data, key=lambda x: x.get("stars", 0), reverse=True)
    elif sort_by == "forks":
        return sorted(deduplicated_data, key=lambda x: x.get("forks", 0), reverse=True)
    elif sort_by == "issues":
        return sorted(deduplicated_data, key=lambda x: x.get("issues", 0), reverse=True)
    elif sort_by == "updated":
        return sorted(deduplicated_data, key=lambda x: x.get("raw_updated_at", ""), reverse=True)

    return deduplicated_data

def export_json_data(repos_data: list[dict[str, Any]]) -> None:
    """Export repository metadata for the Next.js dashboard merge step."""
    try:
        metadata = [
            {
                "repo": repo["name"],
                "stars": repo["stars"],
                "forks": repo["forks"],
                "issues": repo["issues"],
                "lastUpdated": repo["raw_updated_at"],
                "lastCommit": repo.get("commit_sha"),
            }
            for repo in repos_data
        ]
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        logger.info(f"Repository metadata exported to {METADATA_FILE}")
    except Exception as e:
        logger.error(f"Error exporting JSON data: {e}")

def format_badge_timestamp() -> str:
    """Format timestamp for shields.io Last Updated badge."""
    timestamp = datetime.now(UTC).strftime("%Y--%m--%d %H:%M UTC")
    return timestamp.replace(" ", "%20").replace(":", "%3A")

def main() -> int:
    """Main function to update repository status"""
    args = parse_arguments()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting GitHub Repository Status Update")

    if not GH_TOKEN:
        logger.error("GitHub token not found. Set the GH_TOKEN environment variable.")
        return 1

    try:
        # Check rate limit before starting
        try:
            check_rate_limit(args.force)
        except ApiRateLimitExceeded as e:
            if not args.force:
                logger.error(f"GitHub API rate limit exceeded: {e}")
                return 2
            logger.warning("Forcing execution despite rate limit")

        # Load repositories
        repositories = load_repos(args.config)
        if not repositories:
            logger.warning("No repositories found in configuration")
            return 0

        # Fetch repository status
        statuses = []
        failed = []

        for repo in repositories:
            try:
                logger.info(f"Processing repository: {repo}")
                status = fetch_repo_status(repo)
                if status:
                    statuses.append(status)
                else:
                    failed.append(repo)
            except ApiRateLimitExceeded:
                logger.error("Rate limit exceeded, stopping processing")
                break
            except Exception as e:
                logger.error(f"Error processing {repo}: {e}")
                failed.append(repo)

        # Remove this sorting step - we'll handle it later with deduplication
        # (This will be handled in the section where we generate the markdown)

        # Generate markdown and update README
        if statuses:
            # Export data to JSON for potential web interface use
            export_json_data(statuses)

            # Log the number of repositories before and after deduplication
            logger.info(f"Processing {len(statuses)} repositories (before deduplication)")

            # Sort and deduplicate repositories
            sorted_statuses = sort_repos(statuses, args.sort)
            logger.info(f"Processed {len(sorted_statuses)} repositories (after deduplication)")

            # Generate markdown content
            markdown = generate_repo_section(sorted_statuses)

            badge = BADGE_TEMPLATE.format(format_badge_timestamp())
            total_stars = sum(repo.get("stars", 0) for repo in sorted_statuses)

            # Update README
            if inject_into_readme(
                badge,
                markdown,
                args.readme,
                repo_count=len(sorted_statuses),
                total_stars=total_stars,
            ):
                logger.info(f"Updated {len(sorted_statuses)} repositories in README")
            else:
                logger.error("Failed to update README")

        # Report results
        if failed:
            logger.warning(f"Failed to process {len(failed)} repositories: {', '.join(failed)}")

        logger.info("Repository status update completed")
        return 0

    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        return 3

if __name__ == "__main__":
    sys.exit(main())
