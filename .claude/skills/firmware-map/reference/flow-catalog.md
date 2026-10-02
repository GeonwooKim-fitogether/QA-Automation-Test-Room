# Flow Catalog

Flowcharts make the runtime behavior legible. To stay reproducible, **always emit the four
mandatory flows** below, then add domain flows when the matching subsystem is present. Each flow
renders in the Explorer's "Flows" tab from the `FLOWS` array in the template script.

## FLOWS array format (in the rendered HTML)

```js
{ t: "Title",
  d: "One-line description of what the flow shows and why it matters.",
  steps: [
    ["label", "sub-note"],                 // normal step
    ["label", "sub-note", 1],              // 1 = spawns a task OR is a pipeline stage (green left bar)
    ["label", "branch?", "q"],             // "q" = decision/branch (dashed amber border)
    ["label", "sub-note", 0, "설명…"],     // index 3 = per-step plain explanation (shown under the
                                            //           chart when 보충설명 is on; pad index 2 with 0
                                            //           if the step has no flag)
  ]
}
```

Every step SHOULD carry an index-3 plain-language explanation of what that box means/does. The
template lists these as a "박스별 설명" block under the chart (toggle-controlled), then closes with
the flow's `eli` as a "▸ 흐름 정리" wrap-up.

Keep step labels short (a function or component name). Put the detail in the sub-note. A flow is a
single left-to-right chain; if the real control flow branches, represent the branch as a `"q"` step
and continue with the dominant path (describe alternatives in the chat summary, not the box).

## Mandatory flows (always produce these four)

1. **Boot sequence** — from reset/`app_main`/`main` to the steady-state task loop. Show init order,
   peripheral bring-up, the diagnostic/health gate, and the mode branch (e.g. host/debug vs normal).
2. **Primary data path** — the reason the device exists. Acquisition → encode/transform → buffer →
   persist → transmit. For a tracker: sensors → encoder → SD/flash → upload. Adapt the endpoints to
   the project.
3. **Power / shutdown paths** — every trigger (button, low battery, storage error, watchdog, panic)
   converging on the orderly shutdown sequence; mark the trigger as a `"q"` step.
4. **Control / state machine** — the inputs (flags/events) → decision logic → states → observable
   effect (LEDs, mode, behavior).

## Domain flows (add when the subsystem exists)

- **Positioning / RTK correction** — correction source (NTRIP/base) → transport → frame parse →
  integrity check (`"q"`) → inject to receiver → fix → fusion update.
- **OTA / firmware update** — trigger → download/transfer → verify → flash → mark-valid/rollback.
- **Sensor fusion** — sensor sample → predict → measurement update → output state.
- **Host/debug command** — command frame in → parse/validate (`"q"`) → dispatch → response.

## Depth control (how deep to go)

- Default: component/function level (one box per meaningful function or module action).
- If the user asks to go deeper on one flow, drop to **statement/branch level** for that flow only:
  one box per significant call or conditional, with the function name in the label and the
  condition in the sub-note.
- Never exceed ~9 boxes in a single chain; split into two flows instead.
