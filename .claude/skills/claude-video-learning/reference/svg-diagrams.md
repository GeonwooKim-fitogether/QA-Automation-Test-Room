# SVG Diagram Reconstruction — patterns & shared style

**Use this for CONCEPT/STRUCTURE figures** (architectures, taxonomies, workflows, relationships, mental models). For "show the actual screen" figures — live UI, product/result, before→after in a screencast/demo — **do NOT redraw; use a cleaned real screenshot** instead (SKILL.md Step 6b · pipeline.md §6). Reconstructing a live demo into boxes kills its authenticity.

The core craft for the SVG path: **understand each source diagram (in hi-res), then redraw it as clean inline SVG** in the document palette. Crisp at any zoom, on-brand, no webcam/subtitle/cutoff, text selectable, HTML self-contained.

## Rules

1. **Look before you draw.** Read the 1280px frame of the diagram. Reproduce its real structure — nodes, edges, labels, groupings — not a vague impression.
2. **Every color class you use MUST be defined.** An SVG `<rect>`/`<circle>` with an undefined class falls back to **solid black fill** — a silent, ugly bug. After rendering, rasterize and look (see pipeline.md §5).
3. **One shared style + one global arrowhead marker.** Define once; reuse across all diagrams for visual consistency.
4. **Pick the form per diagram:** box-grid · flow · tree · node-link graph (below).
5. Keep `viewBox` width ~1000; let CSS scale it to the card. Cap height in print so a wide figure can't overflow the page.

## Shared CSS (put in the document `<style>`)

```css
.dg { width:100%; height:auto; display:block; }
.dg text { font-family:"Malgun Gothic","Pretendard",sans-serif; fill:#20302c; }
.dg .title { font-weight:800; fill:#11403a; text-anchor:middle; }
.dg .nt { font-weight:700; text-anchor:middle; }          /* node title */
.dg .sub { text-anchor:middle; fill:#45554f; }            /* sub label */
.dg .ls { text-anchor:start; fill:#45554f; }
.dg .el { fill:#0b6b53; font-weight:700; text-anchor:middle; }   /* edge label (accent) */
.dg .gl { fill:#7c8b86; font-weight:700; text-anchor:middle; }   /* edge label (neutral, for demo graphs) */
.dg .edge { stroke:#8a9893; stroke-width:2; fill:none; }
.dg .dash { stroke:#9aa8a3; stroke-width:1.6; fill:none; stroke-dasharray:5 4; }
.box { stroke-width:2; }
/* node fills — DEFINE EVERY ONE YOU USE */
.g{fill:#d9f0df;stroke:#46a566;} .o{fill:#fde4cf;stroke:#e0833a;} .b{fill:#d8e9fb;stroke:#4a90d9;}
.y{fill:#fdf0c6;stroke:#d4ab35;} .p{fill:#e7dcfb;stroke:#8a63d2;} .wt{fill:#fff;stroke:#b9c6c1;}
.tl{fill:#d3f0eb;stroke:#3aa99a;} .pk{fill:#f3c8ec;stroke:#cc6bb3;} .br{fill:#c98b7f;stroke:#9c6354;}
.lime{fill:#cfe89a;stroke:#8cb43f;} .cyn{fill:#74e0cf;stroke:#36b6a0;}
/* light container tints */
.cb{fill:#eef6fd;stroke:#4a90d9;} .cg{fill:#eef8f0;stroke:#46a566;} .cp{fill:#f4eefc;stroke:#8a63d2;} .cy{fill:#fdf8e8;stroke:#d4ab35;}
@media print { .dg { max-height:152mm; } }
```

## Global arrowhead marker (once, in `<body>` top)

```html
<svg width="0" height="0" style="position:absolute"><defs>
  <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
          orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#8a9893"/></marker>
</defs></svg>
```
Reference it on any edge: `<line class="edge" … marker-end="url(#ar)"/>`.

## Patterns

### A. Box-grid (taxonomies, type lists — e.g. POLE+O)
Rounded `<rect class="box COLOR">` + `<text class="nt">` header + `<text class="sub">` rows. Color each box differently for scanability.
```html
<svg class="dg" viewBox="0 0 1000 200">
  <rect class="box g" x="40" y="40" width="280" height="130" rx="16"/>
  <text class="nt" x="180" y="72" font-size="16" fill="#2c7a44">PERSON</text>
  <text class="sub" x="180" y="100" font-size="13">Individual</text>
  <text class="sub" x="180" y="122" font-size="13">Professional</text>
</svg>
```

### B. Flow (pipelines, left→right stages)
Boxes in a row joined by `marker-end` arrows. Stage name `.nt`, sub `.sub`.
```html
<rect class="box g" x="190" y="55" width="150" height="44" rx="11"/><text class="nt" x="265" y="80" font-size="11">Stage 1</text>
<line class="edge" x1="134" y1="77" x2="190" y2="77" marker-end="url(#ar)"/>
```
Horizontal flows fit landscape pages well.

### C. Tree (one parent → children — e.g. Customer→preferences)
Parent box at top; children below; one `<line … marker-end>` per child; relationship name as `.el` near each edge.

### D. Node-link graph (the reasoning trace; demo graphs)
- Group sub-structures in light **container** rects (`.cb/.cg/.cy/.cp`) with a `.ls` label, e.g. "Short-term memory".
- **Draw edges BEFORE nodes** so node fills sit on top of line ends (cleaner joins).
- Circles for entity-graph nodes (`<circle class="box br">`), rounded rects for typed nodes.
- Relationship labels: `.el` (accent) for conceptual diagrams, `.gl` (neutral gray) for "this is the Neo4j browser" demo recreations.
- Place dashed `.dash` paths for secondary relations (e.g. `RETRIEVED_IN`). Use a gentle Bézier (`<path d="M…C…">`) when a straight line would cross other nodes.

Demo screenshots (Neo4j browser, dashboards) → **redraw the graph**, don't screenshot. A clean node-link recreation reads far better and matches the design.

## Lightbox interaction (works because figures are SVG)
The template's lightbox clones the clicked `figure svg` into a full-screen overlay — already self-contained, no image files. Keep figures as `figure > svg` so the script (`document.querySelectorAll('figure svg')`) picks them up.

## Sanity checklist before delivering
- [ ] Every `class="box X"` has `X` defined (no black boxes).
- [ ] Edge labels don't overlap nodes/each other.
- [ ] Nothing clipped at the `viewBox` edge.
- [ ] Korean text renders (Malgun Gothic/Pretendard in `.dg text`).
- [ ] You rasterized and *looked at* every page.
