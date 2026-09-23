---
name: sdd-init
description: "Trigger: sdd init. Detect project context, tests, and persistence."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Initialize SDD only after the user approves the persistence mode.

## Hard Rules
- Detect stack, architecture, test runner, quality tools, and strict TDD; never guess.
- Do not create OpenSpec in Engram or none mode.
- Build `.atl/skill-registry.md` only when project writes are approved.

## Execution Steps
Inspect project and CI files, resolve testing capabilities, initialize the
selected store, and persist project context plus testing capabilities.

## Output Contract
Return the standard SDD envelope with stack, test command, strict TDD, artifacts, and risks.

## References
- [references/init-details.md](references/init-details.md)
- [../_shared/persistence-contract.md](../_shared/persistence-contract.md)
