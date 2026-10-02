---
name: hardware-map
description: Turn a hardware schematic (회로도 PDF) into a consistent, reproducible interactive understanding tool — a Top-down block relationship diagram for non-experts and DRBFM circuit review. Renders a clickable node-link graph of functional blocks + buses, a per-block detail panel (role, connections, workflows), and an L3 view with the cropped schematic + In→Out flow + a complete "every part with its role" table (spec summaries + datasheet links), from a bundled fixed engine so every run looks identical. Use when the user gives a schematic/PCBA and wants to map it, explain what connects to what, review whether the circuit/parts are well-chosen, or generalize the CLBX-6A/CLBY-2A "understand" tool to a new product.
label_ko: 하드웨어 맵
summary_ko: 스키매틱(회로도) PDF를 넣으면 기능 블록·버스 관계도 + 블록별 상세(역할·연결·워크플로우) + L3(크롭 회로도·흐름·전 부품 역할표)를 담은 인터랙티브 HTML을 매번 동일한 형태로 생성합니다. DRBFM 회로 리뷰·비전문가 이해용.
---

# Hardware Map

Produce a **reproducible** Top-down understanding of a hardware design from its **schematic**. The
visual output (a self-contained interactive HTML) must look and behave the **same every run** — only
the data changes. Determinism comes from the bundled **engine** (`assets/hardware_map_engine.py`) +
fixed schema. **Do not hand-author a new HTML/CSS/JS layout each time** — fill data, run the engine.

## Design principle

```
schematic.pdf → [render_crops.py] → crops.json + imgs.json ─┐
              → [extract_parts.py] → parts.json ────────────┤→ hw_data.py → [hardware_map_engine.py] → <model>-understand.html
              → (read + author blocks/edges/workflows) ─────┘   (per product)      (FIXED engine)          (the deliverable)
```

The **engine is fixed** (levels, graph, panel, crop-click-to-fullwidth, colors, interactions). You
author one file, `hw_data.py`, plus a `crops_def.py`. The bundled `*.example.py` files are the real
**CLBX-6A** data — copy and adapt them; do not start from scratch.

## The fixed output (what the engine renders — don't redesign it)
- **L0 제품**: central hub block (usually the MCU) shown with the whole graph + its detail + the L3 view.
- **L1 관계도**: full-width block relationship graph (nodes=blocks, edges=buses, arrows=direction → / ↔).
- **L2 블록 상세**: full-width panel — ① what it does ② what it connects to (bus-grouped) ③ workflows.
- **L3 회로도·부품**: cropped schematic + In→Out flow + **부품별 역할** table (every RefDes in the crop,
  with value + role + collapsible spec summary + datasheet link). **Click the crop image → schematic
  fills full width** (it's small otherwise).

## Absolute rules
- **척추(Spine)**: a part's Rev·Phase changes only via a Change(ECO). Never imply direct edits.
- **Self-contained**: inline CSS/JS + `data:` URIs only. Zero external requests (opens in Jira/Artifacts).
- **No omissions in 부품별 역할**: every part visible in a block's crop must appear in its list. This is
  what `extract_parts.py` guarantees (list = crop contents); don't hand-curate a lean subset.
- **Monochrome decoration**: keep functional colors (bus palette, semantic red/green) but no decorative
  rainbow/emoji.
- **Data scope**: materials from the HTS shared drive only; PCBA + child-PCB materials + PCBA component
  datasheets + battery datasheet (from BOM) are the source.

## Procedure

### Step 1 — Get the schematic + set up a work dir
Obtain the schematic PDF (user upload, or Google Drive via the drive tools — large downloads are
auto-saved to disk). Make a work dir and copy the bundled assets into it:
```
cp <skill>/assets/hardware_map_engine.py <skill>/assets/render_crops.py <skill>/assets/extract_parts.py  .
cp <skill>/assets/crops_def.example.py crops_def.py
cp <skill>/assets/hw_data.example.py   hw_data.py
```
Confirm the PDF decodes: `python3 -c "import fitz; d=fitz.open('schematic.pdf'); print(len(d), [round(p.rect.width) for p in d])"`.

### Step 2 — Read the schematic and identify the blocks (the real work)
For each sheet, find the **functional blocks** (MCU, power/charger, each sensor, GNSS chain, storage,
connectors, LED…), the **central hub** (MCU), the **buses** between them (I²C/SPI/UART/SDIO/USB/RF/PWR/
CTRL) and each edge's **direction**. When unsure how a block "should" work, consult
`docs/0-arena-reference/` and the component datasheets. Note each block's bounding region on its sheet.

### Step 3 — Author `crops_def.py` (CROP + SHEETS)
Set `CROP[block] = (page_idx, x0,y0,x1,y1)` (fractional 0–1 preferred) for every block, and `SHEETS`
(sheet-group key → page). Block ids MUST match `B` in `hw_data.py`. See `crops_def.example.py`.

### Step 4 — Render crops + full sheets
`python3 render_crops.py schematic.pdf` → `crops.json`, `imgs.json`. Eyeball a couple of crops (a block's
crop should show that block's parts). Tighten rects to reduce cross-quadrant bleed if needed.

### Step 5 — Extract the complete parts list
Fill the IC/connector `VAL` map in `extract_parts.py` (passives auto-resolve from footprints), then
`python3 extract_parts.py schematic.pdf` → `parts.json`. Verify a block the user cares about (e.g. IMU)
lists **every** RefDes shown in its crop (U-chips, R40–R46, C-caps…). The extractor rejects LGA/BGA
pin-name false positives and de-dups each RefDes to its real placement — trust it over manual lists.

### Step 6 — Author `hw_data.py` (FIXED SCHEMA — see `reference/data-schema.md`)
Fill, using `hw_data.example.py` as the worked reference:
- `META` — model/title/doc/source/basis/sheet_titles.
- `B` — blocks: `id: (name, chip, group, one-line-role, x, y, sheet_key)`. x,y are SVG coords in a
  `0 0 980 760` viewBox; lay the hub center-ish and group related blocks.
- `E` — edges: `(a, b, bus, label, direction)`, direction ∈ `'ab' | 'ba' | 'bi'`.
- `WF` — workflows: `(name, [block ids in flow order], one-line description)`.
- `FLOW` — per block In→Out: `{block: {'in':[[sig,desc]…], 'core':'chip', 'out':[[sig,desc]…]}}`.
- `TUTOR` — `{block: (one-line intro, [])}`.
- `GC/GLAB` (group color/label), `BC/BLAB` (bus color/label), `SPEC` (optional block specs, may be `{}`).
- `DS` / `PART_DS` / `PART_SPEC` — datasheet links + component spec cards (DRBFM DB seed). Match by token.
- `PARTS` — build from `parts.json` (complete list) overlaid with rich "무엇+왜+관계" descriptions for the
  key ICs and specially-called-out passives; the example shows the `_RICH` + `_BROLE` overlay pattern.

### Step 7 — Generate
`python3 hardware_map_engine.py` → writes `META['out']` (or `OUT`). It prints `blocks/edges/bytes`.

### Step 8 — Verify in a browser (required)
Headless Playwright (Chromium at `/opt/pw-browsers/chromium`): load the HTML, assert **no `pageerror`**,
then check: L0 opens on the hub; clicking blocks highlights connections; L1/L2/L3 pills work; the crop
image click expands to full width; and a spot-checked block's 부품별 역할 lists **all** its crop parts.

### Step 9 — Ship
Publish with the Artifact tool (favicon `🔧`) and give the link. If working in this repo, write the HTML
to `prototypes/<model>-understand.html`, then commit + push to the working branch. Keep prose in chat,
not in the HTML.

## Engine contracts (measured — break these and the page renders blank)

These are requirements the fixed engine puts on **your data**, and none of them were written down until
LHP3-1C (Live Hub LTE, 2026-08-18) hit all four in one sitting. The failure mode is what makes them
worth listing: the file is generated at a normal size and the run log says `blocks: N edges: M`, but the
page is empty or one level is dead. Size and logs cannot tell you.

- **The hub block's id must be `mcu`.** The engine hardcodes `const CENTER='mcu'` and gives that id a
  bigger node and its own CSS. On a board whose brain is not an MCU (a carrier board, a passive break-out),
  still key the hub `mcu` and put the real name in `B[id][0]` — the name is free text, the id is a contract.
  Otherwise `select()` throws `Cannot read properties of undefined (reading 'g')` and no node renders.
- **`BC`/`BLAB` must define all eight standard bus keys** (`PWR I2C SPI UART SDIO RF USB CTRL`). The legend
  iterates that fixed list, so a missing key is a `KeyError` at generation time. Bus keys never appear on
  screen — only `BLAB` labels do — so when a product's buses differ, map them onto the standard keys and
  write the labels for the product (e.g. key `SDIO` labelled `이더넷 페어`, key `RF` labelled `SIM 인터페이스`).
  Adding non-standard keys works for edges but leaves them out of the legend.
- **Every `PART_SPEC` entry needs `absmax`, `op` and `key`** (an empty list is fine — do not invent values).
  `specCard()` maps all three unconditionally, so a missing one throws `Cannot read properties of undefined
  (reading 'map')` **only when L3 opens.** L0~L2 look perfect, which is why this one hides. Three lines at
  the end of `hw_data.py` close it:
  ```python
  for _toks, _spec in PART_SPEC:
      for _k in ('absmax', 'op', 'key'):
          _spec.setdefault(_k, [])
  ```
- **`extract_parts.py`'s `REF`/`FOOTP` cover `U R C L D Q Y SW CON ANT NR J E MP` only.** Boards using other
  prefixes (`MN` MOSFET, `TVS`, `EC` electrolytic, `FB` bead, `T` transformer, `TP` test point, `LED`) need
  them added — and each prefix also needs its footprint pattern in `FOOTP`, or the part is treated as a pin
  name and dropped. Widening `T` has one side effect: IC pin names like MP8009's `T2P` get picked up as
  parts. Drop those in the data (`_auto[block] = [p for p in _auto[block] if p[0] != 'T2P']`), not in the
  engine.

One more thing that is not a contract but costs an hour if you learn it the hard way: **read part values
from the schematic's line order, not from coordinates.** Picking "the nearest word below the RefDes" grabs
pin names; splitting the sheet text into lines and taking the line after the RefDes was nearly always right
(120 of ~120 parts on a 6-sheet board, versus a badly polluted map from the coordinate method).

## Notes
- The engine writes `SHTITLE` from `META['sheet_titles']`; blocks with no crop gracefully fall back to
  the full sheet (or hide the image). CLBY-2A (image-less blocks) is handled the same way.
- If a datasheet is missing, web-search it and extract abs-max / op / key specs into a `PART_SPEC` entry
  — the closed-loop DRBFM value is checking each part's ratings against how the real circuit uses it.
