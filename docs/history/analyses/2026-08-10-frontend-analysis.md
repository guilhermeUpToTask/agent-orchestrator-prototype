# The console, measured

**2026-08-10.** Phase 9 task 2. Written before any refactor, from measurement
rather than impression, so task 3's plan is derived rather than asserted.

Every claim below names a file and a line. Where a starting hypothesis turned
out to be **wrong**, it is recorded as wrong rather than quietly dropped —
that is most of the value here, because the roadmap's own scoping table listed
several observations that do not survive contact with the code.

## The headline: it is in better shape than its line counts suggest

The roadmap scoped this phase from a table of raw sizes — `api.ts` 906 lines,
`queries.ts` 904, `GatePanel.tsx` 636, "119 inline styles". Read as a list of
god-objects, that table is misleading, and three of its implied findings are
false:

| Starting hypothesis | Verdict |
|---|---|
| `api.ts` is a 906-line god module | **Wrong.** 68 exported one-line functions over one typed `request<T>` helper (`api.ts:59`). This is a port, and it is a good one. |
| The 35 `react-query` hooks need a query-key factory | **Already exists** and is comprehensive — `queries.ts:120`, 20 entries, used by 67 of 70 keys. |
| `GatePanel.tsx` is a 636-line component | **Wrong.** Nine components in one file, each small. The real finding is *which* nine (below). |
| The generated-types boundary may be drifting | **Holding.** Exactly one file imports `types/generated` — `types/ui.ts:33`. Nothing else touches the transport contract. |
| A bare `qc.invalidateQueries()` nukes the cache | **Deliberate and correct** (`queries.ts:778`): it runs only on SSE *reconnect*, where events during the gap are genuinely lost. |

**Do not refactor any of the above.** Time spent there is time not spent on
what follows.

## Finding 1 — there are two styling systems, and the split is not where the docs say

`styles/tokens.ts:1` states its own contract:

> The source of truth for design tokens is CSS variables in global.css. This
> file is (1) a thin typed mirror for the few places React needs raw values
> (React Flow edges, minimap colors)…

That is the intent. The measurement:

- **119 inline `style={{…}}` sites** across the app.
- **89 of them (75%) are in 9 components that have no CSS module at all.**
- Only **33** of those are the documented React Flow exception (`PlanCanvas`
  14, `TaskNode` 10, `GoalGroupNode` 9), which genuinely cannot use classes.
- The other **56** have no such justification: `ChatPanel.tsx` (25),
  `ConsoleDock.tsx` (19), `PhaseTimeline.tsx` (7), `Plans.tsx` (3),
  `StatusBadge.tsx` (1), `CycleEvidenceSummary.tsx` (1).

So "a thin mirror for a few places" is the **primary styling mechanism for at
least three whole components**. `ChatPanel` and `ConsoleDock` have no
stylesheet whatsoever.

Worse, the two systems interleave inside single declarations —
`ChatPanel.tsx:40`:

```tsx
style={{ fontSize: 'var(--fs-micro)', fontFamily: tokens.fontMono, color: tokens.textMuted }}
```

A CSS custom property and two TypeScript token values in one object. A reader
cannot tell from a component which system owns a given property, and neither
can a design pass.

**This is the finding that must be settled before task 4 begins**, because a
visual system laid on top of an unresolved split produces a third system.

## Finding 2 — `GatePanel.tsx` serves two lifecycles at once

`GatePanel.tsx:31` dispatches on whether the plan is cyclic:

```tsx
const cyclicGate = gate && ['intent', 'cycle_draft', 'cycle_completion'].includes(gate.subject_type);
…
{!cyclicGate && plan.legacy_phase != null && plan.phase === 'awaiting_review' && <PreExecutionGate …/>}
{!cyclicGate && plan.legacy_phase != null && plan.phase === 'review'          && <PostExecutionGate …/>}
```

**205 of its 636 lines (lines 432–636) — `RoadmapDoc`, `RoadmapEditor`,
`PreExecutionGate`, `PostExecutionGate` — serve only the legacy nine-phase
projection**, which `CLAUDE.md` describes as a read/transition compatibility
layer for migrated rows and existing clients, and which is *never* the
authority for a plan with an active cycle.

That is a third of the file, reachable only by pre-cyclic plans, sitting in the
same module as the gate every current operator uses. It is not dead code — the
projection is real — but it is a separate concern with a separate lifetime, and
carrying it here is what makes the file look like a monster when its components
are individually fine.

## Finding 3 — accessibility: interactive content nested inside a button

`ConsoleDock.tsx:103–141`. A `<button>` whose children include two
`<span role="button" tabIndex={0}>` toggles:

```tsx
<button onClick={toggleConsole} aria-expanded={consoleOpen}>
  <span>AGENT EVENTS {count > 0 && `· ${count}`}</span>
  {selectedTaskId && <span role="button" tabIndex={0} onClick={…}>SELECTED TASK</span>}
  <span role="button" tabIndex={0} onClick={…}>FAILED ONLY</span>
</button>
```

Four separate problems, all provable:

1. **Invalid HTML.** Interactive content may not nest inside a `button`.
2. **The accessible name absorbs the children.** Driving the live console with
   `playwright cli snapshot` returns
   `button "AGENT EVENTS · 1 FAILED ONLY"` — one control announcing the labels
   of the controls inside it.
3. **Hand-rolled button semantics.** `role="button" tabIndex={0}` plus manual
   `Enter`/`Space` key handling re-implements what `<button>` provides, and
   the Space handler does not prevent the page-scroll default.
4. **Toggles that never say they are toggles.** Both are on/off state and
   neither carries `aria-pressed`, so a screen-reader user cannot tell whether
   the filter is active.

Broader measurement: **29 `<button>` elements carry no `aria-label`** and rely
on their text content. That is correct where the text is a real label and a
defect where the content is an icon — the Phase 9 e2e suite already fails to
address two such controls by name (the chat send button and the chat panel
toggle), which is why it reaches them another way.

## Finding 4 — two of the three plan tabs have no heading

`Goals.tsx` and `Activity.tsx` render no `<h1>`–`<h6>` at all. `Goals.tsx:21`'s
only landmark is a `<p role="alert">` for the empty state. `Agents.tsx:53` has
an `<h2>`.

A reader navigating by headings — the primary screen-reader strategy for
orienting on a page — lands on two of three tabs with nothing telling them
where they are. Already pinned as a known gap in
`e2e/cycle/surfaces.spec.ts`, which asserts today's behaviour and says to
tighten when the headings exist.

## Finding 5 — the composer can only create the first project

`Plans.tsx:141`:

```tsx
{projects.length > 0 ? <Select … /> : <inline create form />}
```

The plan composer offers a way to create a project **only while there are
none**. From the second project onward the operator must already know to go to
Settings → Projects. Nothing on the composer says so.

This was found by writing the browser suite: two specs could not be given
independent projects through the UI, and the helper had to create them over the
API and document why (`e2e/cycle/helpers.ts`).

## Finding 6 — state density is concentrated in four settings sections

Components with more than four state/effect hooks:

| File | hooks |
|---|---|
| `settings/SetupSection.tsx` | 15 |
| `settings/ProjectsSection.tsx` | 15 |
| `settings/ProvidersSection.tsx` | 14 |
| `settings/AgentsSection.tsx` | 14 |
| `components/DetailPanel.tsx` | 13 |
| `settings/CapabilitiesSection.tsx` | 9 |
| `components/GatePanel.tsx` | 9 |

The four settings sections are the same shape: a list, an inline create form, a
per-row edit form, and pending/error state for each mutation. **This is where
SOLID has something concrete to say** — not as an acronym, but because these
four files each hold four responsibilities and the fourth copy of a pattern is
where the abstraction finally pays. Everything else on this list is a single
cohesive concern with legitimately many pieces of state.

Three query keys bypass the factory as inline literals — `queries.ts:212`
(`cycleReview`), `:225` (`reviewPatch`), `:672` (`forge`) — against 67 that use
it. Small, and worth fixing only while touching those lines anyway.

## What this means for task 3

Ordered by payoff, and deliberately short. The measurement does not support a
sweeping refactor, and proposing one anyway would be the "evidence-free
assertion this roadmap exists to prevent".

1. **Settle the styling split** (Finding 1). Decide the rule — CSS modules for
   layout and appearance, `tokens.ts` only where React needs raw values — then
   give `ChatPanel`, `ConsoleDock` and `PhaseTimeline` stylesheets and migrate
   their 51 inline styles (the remaining 5 are one-liners in three other files). Prerequisite for task 4.
2. **Split the legacy gates out of `GatePanel`** (Finding 2) into a
   `LegacyGatePanel`, leaving the cyclic path alone. Pure move, no behaviour
   change, ~205 lines.
3. **Fix the nested-button control** (Finding 3): three sibling buttons in a
   toolbar rather than buttons inside a button, with `aria-pressed` on the
   toggles.
4. **Extract the settings-section pattern** (Finding 6) once the four are read
   side by side — and only if the shared shape survives that reading. A
   premature abstraction over four superficially similar forms is a worse
   outcome than four honest forms.
5. **Headings for `Goals` and `Activity`**, and a composer affordance for the
   second project — both small, both behaviour changes, both belong in task 4
   with their e2e assertions tightened in the same commit.

**Not in scope, on the evidence:** `api.ts`, the `keys` factory, the
generated-types boundary, and `queries.ts`'s structure. They were on the
suspect list and they came back clean.
