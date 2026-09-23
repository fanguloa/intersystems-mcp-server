---
name: sdd-tasks
description: "Trigger: SDD tasks. Produce ordered reviewable implementation work units."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Decompose complete specs and design into implementation tasks.

## Hard Rules
- Every task names concrete files, behavior, and verification.
- Order by dependency and keep tests/docs with their behavior.
- Include exact workload lines: `Decision needed before apply`, `Chained PRs recommended`, and `400-line budget risk`.

## Execution Steps
Retrieve proposal, spec, and design; estimate changed lines; split work above the
400-line review budget; and persist `sdd/{change}/tasks`.

## Output Contract
Return phases, work units, delivery decision, dependencies, and the standard envelope.
