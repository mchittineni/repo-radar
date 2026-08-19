#!/usr/bin/env python3
"""
Repository List Sync

Discovers all repositories owned by GH_USERNAME through the GitHub API and
writes them to the configuration file consumed by update_status.py.

Keeps repos.json in sync with the account instead of maintaining it by hand.
Forks and archived repositories are skipped unless explicitly included.
"""

from datetime import datetime, timezone
import requests
import os
import json
import logging
import argparse
import sys
from typing import Any, Dict, List
from pathlib import Path

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
logger = logging.getLogger("repo_sync")

# Configuration
GH_USERNAME = os.getenv("GH_USERNAME", "mchittineni")
GH_TOKEN = os.getenv("GH_TOKEN")
CONFIG_FILE = os.getenv("CONFIG_FILE", str(DATA_DIR / "repos.json"))
API_TIMEOUT = 10  # seconds
PER_PAGE = 100
MAX_PAGES = 20  # safety stop: 2000 repositories

def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Sync repos.json with the repositories owned by GH_USERNAME"
    )
    parser.add_argument(
        "-c", "--config",
        help=f"Path to configuration file (default: {CONFIG_FILE})",
        default=CONFIG_FILE
    )
    parser.add_argument(
        "-u", "--user",
        help=f"GitHub account to discover repositories for (default: {GH_USERNAME})",
        default=GH_USERNAME
    )
    parser.add_argument(
        "--include-forks",
        help="Include repositories that are forks of another project",
        action="store_true"
    )
    parser.add_argument(
        "--include-archived",
        help="Include archived repositories",
        action="store_true"
    )
    parser.add_argument(
        "--include-private",
        help="Include private repositories (requires a token with repo scope)",
        action="store_true"
    )
    parser.add_argument(
        "-n", "--dry-run",
        help="Print the discovered repositories without writing the config file",
        action="store_true"
    )
    parser.add_argument(
        "-v", "--verbose",
        help="Enable verbose logging",
        action="store_true"
    )
    return parser.parse_args()

def fetch_all_repos(user: str) -> List[Dict[str, Any]]:
    """
    Fetch every repository owned by the given account.

    Uses /user/repos when the token belongs to that account so private
    repositories are visible, and /users/{user}/repos otherwise.

    Args:
        user: GitHub account name

    Returns:
        List of raw repository objects from the GitHub API
    """
    auth = (user, GH_TOKEN) if GH_TOKEN else None
    authenticated_user = None

    if GH_TOKEN:
        try:
            me = requests.get(
                "https://api.github.com/user",
                auth=auth,
                timeout=API_TIMEOUT,
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            if me.status_code == 200:
                authenticated_user = me.json().get("login")
        except requests.RequestException as e:
            logger.warning(f"Could not identify authenticated user: {e}")

    own_account = authenticated_user is not None and authenticated_user.lower() == user.lower()
    if own_account:
        url = "https://api.github.com/user/repos"
        params_base = {"affiliation": "owner", "per_page": PER_PAGE, "sort": "updated"}
        logger.info(f"Listing repositories for authenticated account {user}")
    else:
        url = f"https://api.github.com/users/{user}/repos"
        params_base = {"type": "owner", "per_page": PER_PAGE, "sort": "updated"}
        logger.info(f"Listing public repositories for {user}")

    repos: List[Dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        params = dict(params_base, page=page)
        response = requests.get(
            url,
            auth=auth,
            params=params,
            timeout=API_TIMEOUT,
            headers={"Accept": "application/vnd.github.v3+json"}
        )

        if response.status_code == 404:
            logger.error(f"GitHub account not found: {user}")
            raise SystemExit(1)

        response.raise_for_status()
        batch = response.json()
        if not batch:
            break

        repos.extend(batch)
        logger.debug(f"Page {page}: fetched {len(batch)} repositories")
        if len(batch) < PER_PAGE:
            break
    else:
        logger.warning(f"Stopped after {MAX_PAGES} pages; some repositories may be missing")

    logger.info(f"Discovered {len(repos)} repositories owned by {user}")
    return repos

def filter_repos(
    repos: List[Dict[str, Any]],
    include_forks: bool,
    include_archived: bool,
    include_private: bool,
) -> List[str]:
    """
    Apply the inclusion rules and return repository names, most starred first.

    Args:
        repos: Raw repository objects from the GitHub API
        include_forks: Keep repositories that are forks
        include_archived: Keep archived repositories
        include_private: Keep private repositories

    Returns:
        Sorted list of repository names
    """
    kept: List[Dict[str, Any]] = []
    skipped = {"fork": 0, "archived": 0, "private": 0}

    for repo in repos:
        if repo.get("fork") and not include_forks:
            skipped["fork"] += 1
            continue
        if repo.get("archived") and not include_archived:
            skipped["archived"] += 1
            continue
        if repo.get("private") and not include_private:
            skipped["private"] += 1
            continue
        kept.append(repo)

    for reason, count in skipped.items():
        if count:
            logger.info(f"Skipped {count} {reason} repositories")

    kept.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at") or ""),
        reverse=True
    )

    # Preserve order while removing any duplicate names
    names: List[str] = []
    seen = set()
    for repo in kept:
        name = repo["name"]
        if name not in seen:
            names.append(name)
            seen.add(name)

    return names

def write_config(config_file: str, user: str, names: List[str]) -> bool:
    """
    Write the repository list to the configuration file.

    Args:
        config_file: Path to the configuration file
        user: GitHub account the list was generated for
        names: Repository names to record

    Returns:
        True if the file changed, False if it was already up to date
    """
    config_path = Path(config_file)
    payload = {
        "owner": user,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repositories": names,
    }

    previous: List[str] = []
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as file:
                previous = json.load(file).get("repositories", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read existing config file: {e}")

    added = [name for name in names if name not in previous]
    removed = [name for name in previous if name not in names]

    with open(config_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")

    if added:
        logger.info(f"Added {len(added)} repositories: {', '.join(added)}")
    if removed:
        logger.info(f"Removed {len(removed)} repositories: {', '.join(removed)}")
    if not added and not removed:
        logger.info("Repository list already up to date")

    logger.info(f"✅ {config_file} now tracks {len(names)} repositories for {user}")
    return bool(added or removed)

def main() -> int:
    """Main function to sync the repository list"""
    args = parse_arguments()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting repository list sync")

    if not GH_TOKEN:
        logger.warning("GH_TOKEN not set; only public repositories are visible and requests are rate limited")

    try:
        repos = fetch_all_repos(args.user)
        names = filter_repos(
            repos,
            include_forks=args.include_forks,
            include_archived=args.include_archived,
            include_private=args.include_private,
        )

        if not names:
            logger.error(f"No repositories matched the filters for {args.user}")
            return 1

        if args.dry_run:
            logger.info(f"Dry run: {len(names)} repositories would be written to {args.config}")
            for name in names:
                print(name)
            return 0

        write_config(args.config, args.user, names)
        return 0

    except requests.RequestException as e:
        logger.error(f"GitHub API request failed: {e}")
        return 2
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        return 3

if __name__ == "__main__":
    sys.exit(main())
