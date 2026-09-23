---
name: sdd-spec
description: "Trigger: SDD spec. Write requirements and testable scenarios."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Translate approved proposal capabilities into behavior contracts.

## Hard Rules
- Use RFC 2119 strength and Given/When/Then scenarios.
- Describe what, not implementation.
- Copy the full existing requirement before writing a MODIFIED delta.
- Cover happy paths, edge cases, and errors.

## Execution Steps
Retrieve the full proposal, map capabilities to domains, read existing specs,
write full or ADDED/MODIFIED/REMOVED specs, and persist `sdd/{change}/spec`.

## Output Contract
Return domains, requirement/scenario counts, gaps, next phase, and the standard envelope.
