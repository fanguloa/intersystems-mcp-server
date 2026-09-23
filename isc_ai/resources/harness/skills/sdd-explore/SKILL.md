---
name: sdd-explore
description: "Trigger: SDD exploration. Investigate a change without modifying code."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Explore a feature, bug, or refactor before proposal work.

## Hard Rules
- Read actual entry points, related code, and tests.
- Do not modify project files.
- Compare meaningful alternatives and identify risks.

## Execution Steps
Load applicable skills, map current behavior and affected areas, compare options,
recommend one, and persist `sdd/{change}/explore` when tied to a named change.

## Output Contract
Return current state, affected files, options, recommendation, risks, readiness, and the standard envelope.
