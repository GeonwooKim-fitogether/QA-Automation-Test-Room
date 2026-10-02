---
name: progress-dashboard
description: Render a project's live progress as the FITogether Director Dashboard — the same Swimlane board, flow overlay, detail panel, and 4-chip summary used in the subscription-SaaS project, but driven by THIS project's canonical sources. Reads the decision log, decision queue, work-package worklists, and git history (no hand-maintained mirror, so it can't drift), maps them through a per-project content config, and emits a single self-contained HTML you can open as a Claude Artifact, a committed file, or a live local server. Use when someone wants to see project status / progress / "어디까지 됐지", a director/control-plane board, or a work-package & decision overview for any repo (software, hardware, firmware) that tracks work in markdown + git.
label_ko: 진행 대시보드
summary_ko: 프로젝트의 결정 로그·워크리스트·git 기록을 읽어, 구독-SaaS 프로젝트에서 쓰던 Director 대시보드(스윔레인 보드·플로우·디테일·요약 4칩)와 똑같은 형태로 진행상황을 단일 HTML로 렌더합니다. 내용은 프로젝트별 config로 갈아끼우고, Artifact·파일·로컬 서버 어디로든 띄울 수 있습니다.
---

# Progress Dashboard

Render **any** project's status as the Director Dashboard. The visual shell (형태)
is frozen and identical every run — a prebuilt React bundle. Only the **content
(내용)** changes: it comes from the target project's canonical 박제 sources plus a
small per-project config. There is no hand-maintained state mirror, so the board
cannot silently drift out of date.

## Design principle

```
canonical sources ──(adapter)──▶ raw signals
  meta/decisions.md                + dashboard.config.json (content)
  meta/decision-queue.md   ──(buildDashboardData)──▶ DashboardData
  ai-npi/WP-*_Worklist.md  ──(inject into assets/dashboard.html)──▶ deliverable HTML
  git log
```

- **Shell is fixed** — `assets/dashboard.html` (full doc) and `assets/dashboard.body.html`
  (Artifact body) are prebuilt from `ui-src/`. Do **not** hand-edit them or design a
  new layout. The data is variable; the UI is not.
- **Content is per-project** — `dashboard.config.json` in the target repo defines the
  project name, lanes/phases, work-packages, meta items, forever-pending HCP
  boundaries, and plain-language vocabulary. It lives in the project, not in this
  synced skill, so each project owns its own content.
- **Source is pluggable** — two adapters ship today: the default `npi-docs`
  (NPI doc convention: `meta/` + `ai-npi/`) and `status-md` (a single root
  `STATUS.md` with ✅/🟡/❌ status markers, e.g. Hardware-Team-System — see
  [reference/CONFIG.md](reference/CONFIG.md) for its mapping). Select per
  project with `"adapter": "status-md"` in `dashboard.config.json`, or override
  with `--adapter <name>`. For any other source (Jira export, a CSV, a test
  report), add a new file under `core/adapters/` returning the same shape.
  The UI never changes.

## When to use

Use this skill when the user wants to **see project progress / status** in a visual
board: "진행상황 보여줘", "대시보드 띄워줘", "어디까지 됐어", a director/control-plane
view, or a work-package + decision overview. Works for any repo that records work in
markdown + git (software, hardware, firmware).

Do **not** use it to: edit the dashboard UI (that is a code change in `ui-src/` +
rebuild), or to author project content (that is `dashboard.config.json`).

## Procedure

### Step 1 — Locate the project root
Default is the current working directory. The adapter reads `meta/`, `ai-npi/`, and
`git log` relative to it.

### Step 2 — Render
Node ≥ 18 only; no npm install needed at runtime (zero runtime deps).

```bash
# Artifact body (recommended display) — writes .claude/progress-dashboard.body.html
node .claude/skills/progress-dashboard/render.mjs --artifact

# Full standalone HTML — writes .claude/progress-dashboard.html
node .claude/skills/progress-dashboard/render.mjs

# Explicit project root / output / adapter
node .claude/skills/progress-dashboard/render.mjs <projectRoot> --out <file> --adapter npi-docs
```

The command prints the ticket/lane counts, which config it used, and the output path.

### Step 3 — Display it
Pick the path that fits the environment:

- **Claude Artifact (default, works in cloud + local):** publish the generated
  `.claude/progress-dashboard.body.html` with the **Artifact tool** (favicon `📊`).
  It is a self-contained body fragment — no external requests, CSP-safe. Re-run the
  skill to refresh the snapshot.
- **Open the file:** open `.claude/progress-dashboard.html` directly (`file://`,
  IDE preview), or commit it (e.g. to `docs/`) and serve via GitHub Pages.
- **Live local server (port-forwardable cloud envs):**
  `node .claude/skills/progress-dashboard/render.mjs --serve 4173` — regenerates the
  snapshot on every page load, viewable at `http://localhost:4173`.

### Step 4 — Config (content)
If no `dashboard.config.json` exists, the skill **auto-derives** a minimal board (one
lane per discovered worklist) so it never fails — but labels will be raw codes. For a
proper board, create `dashboard.config.json` in the project root. See
[reference/CONFIG.md](reference/CONFIG.md) and
[reference/dashboard.config.example.json](reference/dashboard.config.example.json).
The render output reports `config: auto-derived` vs `config: dashboard.config.json`.

### Step 0 — First run: ask for the project's actors (one-time setup)
The **GROUP: Agent** view groups cards by *who owns the next action*. The right actors
are project-specific (a hardware team is not "Claude Code / GPT"). So **on first run in
a project whose `dashboard.config.json` has no `actors`**, ASK the user before
rendering:

1. *"이 프로젝트의 행위자(역할)는 누구인가요? 예: Director(결정·승인), Hardware Eng(구현),
   Vendor(외주), System(자동)."*
2. *"work-package(실제 작업) 카드는 누가 맡나요?"* → becomes `roles.implementer`.

Then write `actors` (ordered, each with a one-line `outcome`) and `roles` into the
project's `dashboard.config.json`, and render. Subsequent runs reuse the saved actors —
do **not** ask again. If the user declines, the board falls back to the default
four-actor set (Director / Claude Code / GPT / System). See the `actors` + `roles`
section of [reference/CONFIG.md](reference/CONFIG.md).

## Rebuilding the UI shell (rare — maintainers only)

The bundled HTML only changes when `ui-src/` changes. Rebuilding needs a build-time
install of `esbuild` + `react`, `react-dom`, `react-markdown`, `remark-gfm` (NOT
required for normal use):

```bash
node .claude/skills/progress-dashboard/build-ui.mjs --deps <node_modules-with-react> --esbuild <node_modules-with-esbuild>
```

This regenerates `assets/dashboard.html` + `assets/dashboard.body.html`. Never edit
those files by hand.

## What the board shows

- **Swimlanes** per lane (Product phases / Meta / Control Plane), each work-package as
  a card placed in a column by its real state: Backlog → Plan → Development →
  Verification → Done, plus a Review (재검토) lane for outer-loop signals.
- **Column derivation** is automatic from canonical signals: a closed decision (D-NNN)
  → Done; all worklist tasks committed but no closure → Verification (Director Final);
  commits in progress → Development; worklist written, no commits → Plan.
- **4-chip summary** — Last shipped · Now · Awaiting you · Blockers.
- **Decision log + HCP/decision queue** on the Control Plane lane.
- **Flow overlay, detail panel, theme toggle** — all carried from the v3 UI.

## Anti-patterns

- ❌ Hand-editing `assets/dashboard.html` / `.body.html` (regenerate via build-ui).
- ❌ Putting project content (lanes/work-packages) inside this skill folder — it is
  synced/overwritten across projects. Content belongs in the project's
  `dashboard.config.json`.
- ❌ Designing a new layout per project. The whole point is one frozen shell, content
  swapped via config.
- ❌ Committing `node_modules` or the generated `.claude/progress-dashboard*.html` into
  this skill.
