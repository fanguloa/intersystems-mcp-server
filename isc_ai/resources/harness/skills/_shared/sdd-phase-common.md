# SDD Phase Common Protocol

Phase agents are executors and must not delegate. Before work, load exact skill
paths supplied by the orchestrator; otherwise use `.atl/skill-registry.md` when
present. Never invent unavailable tools or model routing.

For Engram artifacts, search for the topic key and then retrieve the full
observation; search previews are not source material. For OpenSpec, follow
`openspec-convention.md`. Persist every produced artifact to the selected store.

Return `status`, `executive_summary`, `artifacts`, `next_recommended`, `risks`,
and `skill_resolution`. Protect a 400 changed-line review budget: tasks forecast
the risk and apply requires a resolved slice or explicit size exception.
