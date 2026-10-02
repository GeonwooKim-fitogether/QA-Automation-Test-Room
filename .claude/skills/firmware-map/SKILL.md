---
name: firmware-map
description: Build a consistent, professional interactive understanding of a firmware/embedded C codebase. Wraps the general-purpose `understand` skill (for the raw knowledge-graph scan) and then renders a fixed, reproducible Explorer — a clickable node-link dependency graph, runtime flowcharts, a role-mapping table, and a file index — from a bundled HTML template so the output looks identical every run. Use when the user wants to understand, map, or visualize firmware/embedded source (ESP-IDF, FreeRTOS, STM32, Zephyr, bare-metal C, drivers, RTOS task graphs), or asks for connection mapping, role mapping, or flow charts of a C codebase.
label_ko: 펌웨어 맵
summary_ko: 펌웨어/임베디드 C 코드베이스를 스캔해 클릭 가능한 의존성 그래프·플로우차트·역할 테이블이 담긴 인터랙티브 HTML Explorer를 매번 동일한 형태로 생성합니다.
---

# Firmware Map

Produce a **reproducible** professional understanding of a firmware codebase. The visual
output (an interactive HTML "Explorer") must look and behave the **same every run** — only the
data changes. Determinism comes from the bundled template + fixed schema + fixed flow catalog in
this skill. Do not hand-author a new HTML layout each time.

## Design principle

```
understand skill   →  knowledge-graph.json   →  [enrich]  →  graph-data.json  →  [inject into template]  →  firmware-explorer.html
(upstream, generic)    (raw nodes/edges)         (this skill)   (fixed schema)        (bundled, fixed UI)        (the deliverable)
```

The `understand` skill stays untouched so its upstream updates keep flowing. This skill only
**consumes** its graph and **renders** it through a fixed template. The variable part (scan
results) is data; the fixed part (layout, interactions, flow types, role fields) is the template
and this procedure.

## Procedure

### Step 1 — Run the upstream scan
Invoke the `understand` skill on the target firmware so it builds `.claude/knowledge-graph.json`.
Let it do the raw file/function/dependency extraction. Do **not** duplicate its work.

If `understand` is unavailable, fall back to scanning directly, but still emit the same enriched
schema below.

### Step 2 — Identify the project shape (cheap, before fanning out)
- Find the real project source dir (e.g. `main/`, `src/`, `app/`) and **exclude vendored
  frameworks** (ESP-IDF, HAL, `components/`, `third_party/`, vendor `*_reg.c` register drivers —
  keep these as nodes but mark them `layer: driver` and `vendor: true`).
- Read the entry point (`app_main`, `main`, the RTOS task table) and the central header
  (`includes.h` / a project-wide API header) to learn the cross-module surface.

### Step 3 — Fan out a scan per subsystem (parallel agents)
Group source files into subsystems and dispatch one `Explore` agent per group. Each agent returns,
for every file: line count, one-sentence purpose, key functions, what it **depends on** (outgoing
calls, by module + hardware/protocol), what **uses it** (incoming), and a layer classification.
Subsystems are project-specific; derive them from directory + naming + role. Typical firmware
subsystems: `system-core` (boot, scheduler, power, watchdog, gpio, util), sensor/acquisition,
positioning, connectivity (wifi/ble/net), storage/logging, display, host/debug interface.

### Step 4 — Build the enriched graph (FIXED SCHEMA)
Write `.claude/graph-data.json` as `{ "nodes": [...], "edges": [...] }`.

Each **node** MUST have exactly these fields:
- `id` — path-relative id, e.g. `main/gps.c`
- `name` — basename, e.g. `gps.c`
- `lines` — integer line count
- `subsystem` — one of the subsystem keys you defined
- `layer` — one of `entry-point | core-logic | infrastructure | driver | utility`
- `summary` — one sentence: what it does + why it exists (plain, jargon-light so a non-author understands)
- `functions` — array of 1–8 key functions. Each entry is EITHER a bare name string
  (`"app_main"`) OR, preferred, an object `{ name, sig, desc }`:
  - `name` — function name (or a group label like `"register accessors"` for vendor driver
    accessor sets / `"nvs helpers"` for helper clusters).
  - `sig` — the real C signature on one line (`void app_main(void)`); for a group, a short
    placeholder like `"(레지스터 읽기/쓰기 래퍼)"`.
  - `desc` — ONE plain-language sentence (match the project's language) saying what the function
    does + when/why it runs; jargon-light, acronyms expanded. The template makes any entry with a
    `desc` a **clickable tag** (in both the Modules tab and the graph detail panel) that expands to
    show `sig` + `desc`. Bare strings render as non-clickable tags. To produce `desc`/`sig`, fan
    out one agent per subsystem to read the ACTUAL source and document each listed function.

Each node SHOULD also have:
- `eli` — one plain-language sentence (HTML allowed, use `<b>` for the role tag) explaining the
  file to a **non-engineer**, ideally with an everyday analogy. This powers the per-tab "Details"
  (보충설명) supplement. Write it so someone who has never seen the code understands the file's
  job. Omit only for trivial vendor files (give them a short one too where useful).

Each **edge** MUST have: `from`, `to` (node ids), `rel` (e.g. `calls | creates-task | uses |
reads | feeds | woken-by | wakes`), `label` (the concrete function/mechanism, e.g.
`encode_gnss_message`). Capture the **major, real** call/ownership relationships — prefer accuracy
over volume, but do not stop at one edge per file.

Also populate these top-level keys consumed by the template:
- `meta.model` — the firmware's product/model name (e.g. `Cell Y1`, `STM32-BMS`). Rendered as a
  badge next to the "Firmware Explorer" title and in the browser tab, so the deliverable is
  self-identifying. Always set it.
- `meta.sub` — one-line project descriptor shown under the title.
- `meta.subsystems` — **REQUIRED for a clean output.** The subsystem palette + display labels,
  in display order: `{ "<key>": ["<label>", "<#hexcolor>"], ... }`. The keys MUST be exactly the
  `subsystem` values used on the nodes. This is what makes the skill generic — the template reads
  the palette/labels from here, NOT from any hardcoded list. (If omitted, the template falls back to
  auto-collecting subsystem keys and assigning colors, using the raw keys as labels — works, but
  labels look like `gps-rtk` instead of `GPS/측위`.) Define subsystems per project; do not reuse the
  Fitogether set blindly.
- `flows` — the flowchart array (Step 6 / flow-catalog format). Each flow may carry an `eli` string
  (plain-language supplement shown when Details is on) and a `phase` string (a short badge like
  `①`, `③·④`, or `🔄 상시` rendered before the flow title to tie it to `flowOverview`'s lifecycle
  numbering). Order the `flows` array to match the lifecycle in `flowOverview`.
- `flowOverview` — a one-screen lifecycle/user-scenario diagram shown ABOVE the flow charts on
  tab ②, tying the flows together. Structure: `{ title, desc, phases:[ { name, sub, flows:[flow
  titles] } ], always:[titles], alwaysNote, any:[titles], anyNote }`. `phases` is the left-to-right
  user lifecycle (e.g. power-on → setup → during use → after → power-off); each phase lists which
  flow(s) are active (chips jump to that flow). `always` = flow(s) running the whole time;
  `any` = flow(s) that can interrupt at any moment. Notes are phrased to read after the chip(s).
  Flow titles MUST exactly match `flows[].t`. Keep `flows[].phase` badges consistent with the
  phase numbering here.
- `beginner` — the "Overview / 쉽게 보기" (tab ④) content, for non-engineers. Structure:
  - `intro: { title, what, analogy }` — what the device is + an everyday analogy.
  - `concepts: [ { q, a, nodes:[ids], flow } ]` — "how does it do X?" cards. `nodes` highlights those
    files in the graph on click; `flow` jumps to that flow by title. Cover the device's main jobs.
  - `narrative: [ { text, chips:[ids] } ]` — a numbered "what happens at boot" story; file names in
    `chips` become clickable jump links (match the node `name` inside `text`).
  - `example: { title, steps:[ { text, node } ] }` — trace ONE concrete piece of data end to end.
  - `glossary: [ { term, def } ]` — plain definitions of every jargon term used (NMEA, EKF, OTA, …).

### Step 5 — Render the Explorer (do NOT rewrite the template)
1. Read the bundled template: `assets/explorer-template.html`.
2. Replace the single token `__DATA__` with the JSON object from `graph-data.json` (inline it as a
   JS object literal: `const G = { ...graph-data.json contents... };`).
3. Write the result to `.claude/firmware-explorer.html`.
4. Publish it with the Artifact tool (favicon `🛰️`) and give the user the link.

The template defines the subsystem palette, the node-link force graph, click-to-isolate behavior,
the merged **Modules** tab (filterable by subsystem + layer; one row per file with role,
layer, line count, and connection targets), and the Flows tab markup. The Flows tab reads a `FLOWS`
array that is **already in the template's script** — you only need to make sure the flows it lists
match the catalog (Step 6).

### Step 6 — Flows (FIXED CATALOG + project-specific)
See `reference/flow-catalog.md`. Always produce the **four mandatory flows** (boot, primary data
path, power/shutdown, control/state) plus any **domain flows** the catalog suggests for what you
found (e.g. RTK correction, OTA update). Each flow is a left-to-right chain of steps; mark decision
steps and task-spawning/pipeline steps per the catalog's conventions. Edit the `FLOWS` array near
the bottom of the rendered HTML if the project's flows differ from the example set.

### Step 7 — Summarize in chat (not in the HTML)
In your response give: a 3–5 sentence architecture summary, the recommended reading order
(entry point → highest in-degree modules → leaves), and the link to the Explorer. Keep prose out of
the HTML; the HTML is the visual, the chat is the explanation.

## Guardrails
- **Reproducibility first.** Same template, same schema, same four mandatory flows, every run. If
  you improve the look, improve `assets/explorer-template.html` so the improvement persists — never
  one-off in the output file.
- State your coverage honestly: edges are the major relationships you verified, not a guaranteed
  exhaustive call graph. If the user needs an exhaustive graph, say so and offer a full-parse pass.
- Keep vendored/auto-generated drivers as nodes but clearly low-emphasis (large `lines`, `driver`
  layer) so they don't dominate the reading order.
