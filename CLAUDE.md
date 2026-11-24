# Working on this repo

Follow `docs/PLAN.md` phase by phase. Run the full test suite before every commit.
Never weaken a guardrail or a test to make something pass.

- Tests: `python -m unittest discover -s tests` (stdlib only, offline, seconds).
- Demo: `python -m spare demo`.
- `spare/core` is the money path: Python standard library only, and it never imports
  `spare/brain` or `spare/bot`. Tests enforce both.
- Money is integer cents everywhere. No floats for amounts.
- Each phase ends with green tests, a commit and a pull request. Don't start a phase
  until the one before it is merged.

## Authorship

Commits are authored by the repository owner only. Do not add `Co-Authored-By`
trailers, session links, or "Generated with" lines to commits, pull requests,
the README or any other file.
