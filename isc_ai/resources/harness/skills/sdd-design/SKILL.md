---
name: sdd-design
description: "Trigger: SDD design. Choose implementation architecture from real code."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Design the approved change before task decomposition.

## Hard Rules
- Inspect affected code and existing patterns before deciding.
- Record choices, alternatives, rationale, file impacts, interfaces, tests, and rollout.
- Surface blocking questions instead of guessing.

## Execution Steps
Retrieve proposal and available spec, inspect the codebase, document technical
approach and data flow, then persist `sdd/{change}/design`.

## Output Contract
Return approach, decisions, files, testing strategy, open questions, and the standard envelope.
