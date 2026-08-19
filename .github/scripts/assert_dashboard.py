#!/usr/bin/env python3
"""
Assert that a freshly generated dashboard is well formed.

The smoke-test job runs the generator against a scratch file, then this script
checks the output actually contains what the dashboard promises. It compares the
generated file against data/repos.json rather than against the committed
README.md, because live star counts and commit dates move between runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FRAGMENTS = (
    "<!-- START_REPO_STATUS -->",
    "<!-- END_REPO_STATUS -->",
    "## 📊 At a glance",
    "## 🏆 Star leaderboard",
    "## ⏱️ Freshly pushed",
    "## 🧬 Language mix",
    "## 📚 Every repository",
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assert_dashboard.py <generated-readme>")
        return 2

    generated = Path(argv[1])
    if not generated.exists():
        print(f"::error::the generator produced no file at {generated}")
        return 1

    text = generated.read_text(encoding="utf-8")
    tracked = json.loads((REPO_ROOT / "data" / "repos.json").read_text(encoding="utf-8"))["repositories"]

    failures: list[str] = []

    for fragment in REQUIRED_FRAGMENTS:
        if fragment in text:
            print(f"  ok    contains {fragment!r}")
        else:
            failures.append(f"generated dashboard is missing {fragment!r}")
            print(f"  FAIL  missing {fragment!r}")

    blocks = text.count("<!-- repo:")
    if blocks == len(tracked):
        print(f"  ok    one detail block per tracked repository ({blocks})")
    else:
        # A repository can disappear mid-run (renamed, deleted, made private),
        # so allow a shortfall of one but never a silent collapse.
        if blocks and abs(blocks - len(tracked)) <= 1:
            print(f"::notice::generated {blocks} blocks for {len(tracked)} tracked repositories")
        else:
            failures.append(f"generated {blocks} detail blocks for {len(tracked)} tracked repositories")
            print(f"  FAIL  {blocks} blocks for {len(tracked)} tracked repositories")

    if "None" in text.replace("_None detected_", "").replace("_None_", ""):
        failures.append("generated dashboard leaked a literal 'None' value")
        print("  FAIL  literal 'None' present in output")
    else:
        print("  ok    no literal 'None' values leaked into the output")

    unresolved = [line for line in text.splitlines() if "{" in line and "}" in line and "img.shields.io" not in line]
    if unresolved:
        failures.append(f"{len(unresolved)} line(s) look like unrendered template placeholders")
        print(f"  FAIL  unrendered placeholders, first: {unresolved[0][:100]}")
    else:
        print("  ok    no unrendered template placeholders")

    if failures:
        print(f"\n{len(failures)} assertion(s) failed:")
        for failure in failures:
            print(f"::error::{failure}")
        return 1

    print(f"\nDashboard smoke test passed: {blocks} repositories rendered.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
