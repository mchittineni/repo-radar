#!/usr/bin/env python3
"""
Repository validation gate for pull requests.

Parses every config file that CI or GitHub itself depends on, then checks the
tracked data files agree with each other. Exits non-zero on the first set of
problems so a pull request cannot merge with an unparseable workflow or a
descriptions file that has drifted from the tracked repository list.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKERS = ("<!-- START_REPO_STATUS -->", "<!-- END_REPO_STATUS -->")

failures: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    """Record one check result and print it in a CI-friendly form."""
    if ok:
        print(f"  ok    {label}")
    else:
        failures.append(f"{label}: {detail}" if detail else label)
        print(f"  FAIL  {label} {detail}")


def validate_yaml() -> None:
    print("YAML")
    paths = sorted((REPO_ROOT / ".github").rglob("*.yml"))
    paths.append(REPO_ROOT / ".pre-commit-config.yaml")
    for path in paths:
        rel = path.relative_to(REPO_ROOT)
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
            check(str(rel), True)
        except yaml.YAMLError as exc:
            check(str(rel), False, str(exc).splitlines()[0])


def validate_json() -> None:
    print("JSON")
    for path in sorted((REPO_ROOT / "data").glob("*.json")):
        rel = path.relative_to(REPO_ROOT)
        try:
            json.loads(path.read_text(encoding="utf-8"))
            check(str(rel), True)
        except json.JSONDecodeError as exc:
            check(str(rel), False, str(exc))


def validate_toml() -> None:
    print("TOML")
    path = REPO_ROOT / "pyproject.toml"
    try:
        meta = tomllib.loads(path.read_text(encoding="utf-8"))
        check("pyproject.toml", "project" in meta, "missing [project] table")
    except tomllib.TOMLDecodeError as exc:
        check("pyproject.toml", False, str(exc))


def validate_tracked_data() -> None:
    print("Tracked data")
    repos_path = REPO_ROOT / "data" / "repos.json"
    descriptions_path = REPO_ROOT / "data" / "repo-descriptions.json"

    try:
        tracked = json.loads(repos_path.read_text(encoding="utf-8")).get("repositories")
    except (OSError, json.JSONDecodeError) as exc:
        check("data/repos.json readable", False, str(exc))
        return

    check(
        "data/repos.json holds a non-empty repositories list",
        isinstance(tracked, list) and bool(tracked),
        f"got {type(tracked).__name__}",
    )
    if not isinstance(tracked, list):
        return

    check(
        "data/repos.json has no duplicate entries",
        len(tracked) == len(set(tracked)),
        "duplicates present",
    )

    if not descriptions_path.exists():
        notes.append("data/repo-descriptions.json absent; the generator will fall back to GitHub data")
        return

    descriptions = json.loads(descriptions_path.read_text(encoding="utf-8"))

    # An orphan blurb is a mistake (renamed or removed repository). A missing
    # blurb is not: sync_repos.py can discover a new repository at any time and
    # the generator falls back to the GitHub description until someone writes one.
    orphans = sorted(set(descriptions) - set(tracked))
    check(
        "every curated blurb refers to a tracked repository",
        not orphans,
        f"orphans: {', '.join(orphans)}",
    )

    required = {"displayName", "description", "techStack", "status"}
    malformed = sorted(
        name for name, entry in descriptions.items()
        if not isinstance(entry, dict) or not required.issubset(entry)
    )
    check(
        "every curated blurb has the expected fields",
        not malformed,
        f"incomplete: {', '.join(malformed)}",
    )

    missing = [name for name in tracked if name not in descriptions]
    if missing:
        notes.append(f"{len(missing)} tracked repositories have no curated blurb: {', '.join(missing)}")


def validate_readme() -> None:
    print("README")
    readme = REPO_ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    for marker in MARKERS:
        check(f"README.md contains {marker}", marker in text)
    blocks = text.count("<!-- repo:")
    check("README.md contains repository blocks", blocks > 0, f"found {blocks}")


def main() -> int:
    validate_yaml()
    validate_json()
    validate_toml()
    validate_tracked_data()
    validate_readme()

    for note in notes:
        print(f"::notice::{note}")

    if failures:
        print(f"\n{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
            print(f"::error::{failure}")
        return 1

    print("\nAll repository checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
