## What changed

<!-- One or two sentences. What does this pull request do, and why? -->

## Type of change

- [ ] 🐛 Bug fix — the tracker did the wrong thing
- [ ] ✨ Feature — new data, new dashboard section, new flag
- [ ] 🎨 Dashboard/UI — changes how the generated README looks
- [ ] 📝 Docs — README/INSTALLATION/CONTRIBUTING only
- [ ] 🔧 Tooling — workflow, pre-commit, dependencies
- [ ] ♻️ Refactor — no behaviour change

## Related issues

<!-- e.g. Closes #12 -->

## How it was verified

<!-- Paste the commands you ran and the interesting log lines. -->

```bash
# python scripts/sync_repos.py --dry-run
# python scripts/update_status.py --sort stars --verbose
# bash -n scripts/run.sh
```

## Generated output

<!-- Touching the generator? Paste the relevant slice of the new README.md, or a screenshot of
     how it renders on GitHub. -->

## Checklist

- [ ] Ran the affected script(s) locally and read the log
- [ ] No secrets, tokens, or personal data in the diff
- [ ] Commit messages follow Conventional Commits (`feat(dashboard): ...`)
- [ ] Docs updated if behaviour or flags changed
- [ ] `pre-commit run --all-files` passes (or I explained why not)
- [ ] Generated files (`README.md`, `data/*.json`) were regenerated rather than hand-edited
