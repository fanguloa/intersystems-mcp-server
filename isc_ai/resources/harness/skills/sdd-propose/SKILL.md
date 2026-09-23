---
name: sdd-propose
description: "Trigger: SDD proposal. Define intent, scope, capabilities, and rollback."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Turn exploration or a clear request into a concise change proposal.

## Hard Rules
- Separate in-scope and out-of-scope work.
- Name new and modified capabilities explicitly.
- Include risks, rollback, dependencies, and measurable success criteria.

## Execution Steps
Retrieve exploration when available, inspect existing specs, write the proposal,
and persist `sdd/{change}/proposal` according to the selected store.

## Output Contract
Return proposal location, intent, scope, approach, risk level, next phase, and the standard envelope.
