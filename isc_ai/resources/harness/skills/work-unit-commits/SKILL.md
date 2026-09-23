---
name: work-unit-commits
description: "Trigger: implementation, commits, PR splitting. Keep changes reviewable by behavior."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Hard Rules
- A work unit is one deliverable behavior, not one file type.
- Keep tests and user-facing docs with the behavior they verify.
- Preserve a clear start, finish, rollback boundary, and review story.
- Above roughly 400 changed lines, split autonomous units or record an approved exception.

## Output Contract
Report each unit's purpose, files, checks, dependencies, and proposed review boundary.
