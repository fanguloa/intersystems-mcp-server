# Strict TDD Apply

For every behavior: run the existing safety net, write a failing behavioral test
first, implement the minimum passing code, triangulate with a distinct case, and
refactor while green. Execute focused tests at each gate. Assertions must call
production code, assert specific outputs, and fail for a wrong implementation.
Reject tautologies, ghost loops, CSS/internal-state assertions, and mock-heavy
tests that should target an extracted pure function. Record task, test file,
layer, safety net, RED, GREEN, triangulation, and refactor evidence.
