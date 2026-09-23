# Strict TDD Verification

Require a TDD evidence row for every implemented task. Confirm listed test files
exist and pass now, triangulation covers distinct scenarios, and modified files
had a safety-net baseline. Audit changed tests for tautologies, assertions that
do not execute production code, ghost loops, trivial empty/type-only checks,
implementation-detail coupling, and excessive mocks. Report test-layer
distribution and changed-file coverage when tools exist; missing optional tools
are not failures.
