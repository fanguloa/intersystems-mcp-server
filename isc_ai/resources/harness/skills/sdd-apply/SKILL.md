---
name: sdd-apply
description: "Trigger: SDD apply. Implement one approved work unit and record evidence."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Implement only assigned tasks after specs, design, and workload decision exist.

## Hard Rules
- Retrieve full artifacts and prior progress before editing.
- Stop when oversized work has no resolved slice or size exception.
- Match existing patterns and never silently deviate from design.
- When strict TDD is active, load `strict-tdd.md` and record cycle evidence.

## Execution Steps
Read context, implement and verify the assigned work unit, mark tasks complete,
merge cumulative progress, and persist `sdd/{change}/apply-progress`.

## Output Contract
Return completed tasks, files, test evidence, deviations, remaining work, PR boundary, and the standard envelope.

## References
- [strict-tdd.md](strict-tdd.md)
