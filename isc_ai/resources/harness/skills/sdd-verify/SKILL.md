---
name: sdd-verify
description: "Trigger: SDD verify. Prove implementation against specs and tests."
license: MIT
metadata:
  author: isc-ai
  version: "1.0"
---

## Activation Contract
Verify an applied change using source inspection and real execution.

## Hard Rules
- Retrieve proposal, spec, design, tasks, and apply progress.
- A scenario is compliant only when a covering test passed.
- Do not fix findings; classify them as CRITICAL, WARNING, or SUGGESTION.
- Load `strict-tdd-verify.md` only when strict TDD is active.

## Execution Steps
Map requirements to code and tests, run relevant test/build checks, inspect design
coherence and task completion, then persist `sdd/{change}/verify-report`.

## Output Contract
Use `references/report-format.md` and return the standard envelope.

## References
- [references/report-format.md](references/report-format.md)
- [strict-tdd-verify.md](strict-tdd-verify.md)
