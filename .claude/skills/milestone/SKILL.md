---
name: milestone
description: Build one milestone from the MVP brief
disable-model-invocation: true
---
Work on milestone $ARGUMENTS from docs/BRIEF.md.

1. Read CLAUDE.md, docs/SPEC.md and docs/PROGRESS.md (if they exist).
2. Write a short plan for this milestone only: files to change, tests, and how you will prove the "Done when" check. Wait for my OK.
3. Implement in small steps and run the tests after each step.
4. Show evidence for the "Done when" check: test output, command output or Playwright screenshots in docs/screenshots/.
5. Use a subagent to review the diff for bugs and missing requirements. Fix real problems only.
6. Update docs/PROGRESS.md (what was built, what could not be run, open issues) and commit.
