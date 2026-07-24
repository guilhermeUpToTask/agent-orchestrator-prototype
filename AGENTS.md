## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Pull request authority

Branches, worktrees, and commits are implementation details; they do not define
pull request boundaries. Agents must not automatically create one pull request
per branch, task, or feature.

Only the user decides when a pull request is created and which completed
features it contains. Before requesting that decision, present a concise
inventory of the completed features, their commits or branches, dependencies,
overlap, validation status, and any recommended grouping. Do not open, close,
combine, retarget, or merge a pull request until the user explicitly authorizes
that exact action and grouping.

Finishing implementation, pushing a branch, or making CI green is not implicit
authorization to create a pull request.
