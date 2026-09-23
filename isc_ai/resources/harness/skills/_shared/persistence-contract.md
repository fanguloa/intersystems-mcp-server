# SDD Persistence Contract

Supported modes are `engram`, `openspec`, `hybrid`, and `none`. Ask before the
first persistent SDD operation. Engram uses deterministic topic keys
`sdd/{change}/{artifact}` and full-observation retrieval. OpenSpec writes only
under `openspec/`. Hybrid writes both. None returns inline and writes nothing.

Automated SDD saves use `capture_prompt: false` when the tool supports it.
Never create OpenSpec files in Engram or none mode. Never claim persistence
succeeded when one side of hybrid mode failed.
