# dashboard.config.json — per-project content schema

This file lives in the **target project root** (or `.claude/dashboard.config.json`),
NOT in the skill folder. It is the only thing you edit per project — it supplies the
"내용" while the dashboard UI stays frozen. JSON, no comments (zero-dependency parse).

If the file is absent, the skill auto-derives a minimal board from discovered
worklists, but labels will be raw codes — write this file for a real board.

## Top-level fields

| field | type | required | meaning |
|---|---|---|---|
| `projectName` | string | yes | Shown in the tree root and summary. |
| `adapter` | string | no | Which data adapter reads this project (`npi-docs` default, or `status-md`). The `--adapter` CLI flag overrides this. |
| `lanes` | Lane[] | yes* | Swimlanes, in display order. *May be omitted when the adapter supplies a `suggestedConfig` (status-md derives lanes from the document structure — see below). |
| `workPackages` | WorkPackage[] | yes* | The cards placed on Product lanes. *Same omission rule as `lanes`. |
| `metaItems` | MetaItem[] | no | Candidate/meta cards on the Meta lane. |
| `metaSprint` | MetaSprint \| null | no | A single highlighted meta card. |
| `architecturalHcps` | ArchHcp[] | no | Forever-pending boundary reminders on Control. |
| `vocabulary` | object | no | `CODE → 친근한 라벨` overrides (e.g. HCP plain names). |
| `actors` | Actor[] | no | Agent-view swimlanes (order + subtitle). See below. |
| `roles` | object | no | Which actor owns each card category. See below. |

### actors + roles (Agent view)

The board's **GROUP: Agent** toggle regroups the same cards by **who owns the next
action** instead of by phase. These two fields make that project-specific.

- **`actors`** — ordered list of `{ "label": string, "outcome"?: string }`. Each entry
  is one Agent swimlane: `label` is the actor name, `outcome` is the lane subtitle
  ("what this actor is responsible for"). Order here = swimlane order. An actor that
  ends up with no cards is omitted automatically.
- **`roles`** — maps card categories to an actor `label` (category-based assignment):

  | role | covers | default |
  |---|---|---|
  | `implementer` | every work-package card | `"Claude Code"` |
  | `director` | decision records, the decision/HCP queue, architectural HCP gates | `"Director"` |
  | `system` | meta sprint + meta items | `"System"` |

Example (hardware): `"roles": { "implementer": "Hardware Eng" }` puts every
work-package under the **Hardware Eng** Agent lane; decisions/approvals stay under
**Director**. If `actors`/`roles` are omitted, the board uses the original four-actor
fallback (Director / Claude Code / GPT / System) — existing configs are unchanged.

### Lane
| field | type | meaning |
|---|---|---|
| `id` | string | Stable id; work-packages reference it via `laneId`. |
| `label` | string | Lane heading. |
| `parent` | `"Product"` \| `"Meta"` \| `"Control"` | Grouping. Product lanes get the phase-status suffix (· ACTIVE / · CLOSED …) and feed the summary. Exactly one `Control` lane is expected (decision log + queue land there). |
| `outcome` | string (optional) | One-line "what this phase is for", shown as the lane subtitle. |

### WorkPackage
| field | type | meaning |
|---|---|---|
| `laneId` | string | Which lane this card sits on. |
| `wp` | string | Primary code, e.g. `WP-001`. Matched against worklists / decisions / commits. |
| `legacy` | string (optional) | Alias code, e.g. `F-001`. Matched too; shown as `F-001 / WP-001`. Set equal to `wp` if there's no alias. |
| `title` | string | Developer-facing title. |
| `plainTitle` | string (optional) | Director-facing headline (큰 글씨). A `director_one_liner` in the spec frontmatter overrides this. |

Column placement is **automatic** from canonical signals — you do not set it.
Closure (D-NNN) → Done; all worklist tasks committed, no closure → Verification;
commits in progress → Development; worklist written, no commits → Plan; else Backlog.

### MetaItem
`{ ip, legacy?, title, plainTitle?, nextAction?, description? }` — rendered as Backlog
cards on the Meta lane (candidates).

### MetaSprint
`{ code, title, plainTitle?, nextAction?, lane?, column?, description? }` — one
highlighted card (defaults: lane `meta`, column `Development`).

### ArchHcp
`{ code, title, anchor, reason, status }` where `status` is `"out-of-cycle"` (absolute
zero this cycle) or `"future"` (trigger TBD). Rendered as Backlog HCP reminders.

## Where the data comes from (npi-docs adapter)

| signal | source file |
|---|---|
| decision log (D-NNN), closures | `meta/decisions.md` |
| pending decisions / HCP gates (DQ-NNN) | `meta/decision-queue.md` |
| work-package task tables (T-NNN) | `ai-npi/WP-*_Worklist.md` (or legacy `F-*_NPI_Worklist.md`) |
| progress (which T-NNN are done) | `git log` cross-referenced with worklists |
| re-review flags | `meta/re-review.md` |
| spec one-liners | `ai-npi/*.md` `director_one_liner` frontmatter |
| last-commit chip + doc links | `git log` + repo markdown scan |

A project whose status lives somewhere else needs a new adapter under
`core/adapters/` returning the same shape; the config and UI are unchanged.

## Where the data comes from (status-md adapter)

For projects that track work in a single root **`STATUS.md`** instead of the
NPI doc convention (e.g. Hardware-Team-System). Select it with
`"adapter": "status-md"` in the config, or `--adapter status-md`.

What it reads and how it maps:

| STATUS.md element | becomes |
|---|---|
| `## N. 제목` sections (`###` subsections merge into their parent) | lanes. The frozen UI accepts at most 5 Product lanes (`phase1`..`phase5`), so with more sections the 4 largest keep dedicated lanes and the rest fold into a fifth "그 외 절" lane. Card codes (`§2B-01`) keep the original section either way. |
| table rows / top-level bullets carrying a status marker | cards. Status → column: `✅`·`배포완료` → Done; `⧖`·`⚠️`·`검토대기`·`✅ 작업완료`(배포 확인 대기) → Verification; `🟡` → Development; `❌`·`⏸️`·`❓` → Backlog |
| `🔀` lines (any nesting) + items under a "다음 결정 포인트" heading | pending-decision cards on the Control lane (Director queue) |
| the item's full text | a pseudo-task shown in the detail panel (source of truth stays STATUS.md) |
| `git log` | last-commit chip |

Because lanes and cards are derived from the document on every render, the
config file should **omit** `lanes`/`workPackages` — writing them by hand would
recreate the hand-maintained mirror this adapter exists to avoid.

Known limits (deliberate):

- **No per-item progress numbers.** STATUS.md has no task tables and no
  item↔commit code scheme, so `🟡` cards show a fixed 1/2 bar meaning "부분
  구현", not a measured ratio. Git-derived progress is not attempted.
- **No decision log.** STATUS.md has no D-NNN log, so the Decision Log list is
  empty; only the pending queue is populated.
- Lines that look like items but carry no status marker are skipped and the
  count is logged to stderr (`[status-md] … skipped`), never dropped silently.
