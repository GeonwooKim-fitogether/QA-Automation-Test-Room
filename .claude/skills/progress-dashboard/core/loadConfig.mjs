// loadConfig.mjs — resolves the PER-PROJECT content config.
//
// This is the "내용 (content)" half of the skill. The UI shell is frozen; the
// structure (project name, lanes/phases, work-packages, meta items, forever-
// pending HCP boundaries, plain-language vocabulary) lives here, OUTSIDE the
// synced skill folder, so each project owns its own content.
//
// Resolution order:
//   1. <projectRoot>/dashboard.config.json   (explicit, wins)
//   2. <projectRoot>/.claude/dashboard.config.json
//   3. auto-derive from the adapter's discovered worklists + decisions
//      (zero-config: every project that follows the NPI doc convention gets a
//       usable board without writing any config)
//
// JSON (not YAML) on purpose: zero runtime dependencies, guaranteed-correct
// parsing. The skill is invoked by an agent that can emit JSON trivially.

import fs from "node:fs/promises";
import path from "node:path";

const CONFIG_NAMES = ["dashboard.config.json", ".claude/dashboard.config.json"];

async function readJsonIfExists(p) {
  try {
    const raw = await fs.readFile(p, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Auto-derive a minimal config from canonical data when no config file exists.
 * One Product lane + one work-package per discovered worklist code, plus a
 * Control lane for the decision log / queue. Labels are derived from the code.
 */
function deriveConfig(adapterData) {
  const worklists = adapterData?.worklists ?? {};
  const codes = Object.keys(worklists).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  const lanes = [];
  const workPackages = [];
  let i = 0;
  for (const code of codes) {
    i += 1;
    const laneId = `phase${i}`;
    lanes.push({ id: laneId, label: `${code}`, parent: "Product" });
    workPackages.push({ laneId, wp: code, legacy: code, title: code, plainTitle: code });
  }
  lanes.push({ id: "control", label: "Control Plane", parent: "Control" });

  return {
    projectName: adapterData?.projectName || "Project",
    lanes,
    workPackages,
    metaItems: [],
    metaSprint: null,
    architecturalHcps: [],
    vocabulary: {},
    _derived: true,
  };
}

/** Fill in any missing optional sections so the builder never sees undefined. */
function normalize(cfg) {
  const r = cfg.roles && typeof cfg.roles === "object" ? cfg.roles : {};
  return {
    projectName: cfg.projectName || "Project",
    lanes: Array.isArray(cfg.lanes) ? cfg.lanes : [],
    workPackages: Array.isArray(cfg.workPackages) ? cfg.workPackages : [],
    metaItems: Array.isArray(cfg.metaItems) ? cfg.metaItems : [],
    metaSprint: cfg.metaSprint ?? null,
    architecturalHcps: Array.isArray(cfg.architecturalHcps) ? cfg.architecturalHcps : [],
    vocabulary: cfg.vocabulary && typeof cfg.vocabulary === "object" ? cfg.vocabulary : {},
    // Agent-view actors. `actors` defines the swimlane order + subtitles;
    // `roles` maps card categories → actor. Defaults reproduce the original
    // Director/Claude Code/System behavior so existing configs are unchanged.
    actors: Array.isArray(cfg.actors) ? cfg.actors.filter((a) => a && a.label) : [],
    roles: {
      implementer: r.implementer || "Claude Code", // work-packages
      director: r.director || "Director",          // decisions / queue / HCP gates
      system: r.system || "System",                // meta sprint / meta items
    },
    _derived: Boolean(cfg._derived),
  };
}

/**
 * Fill structural fields the config file leaves out from the adapter's
 * `suggestedConfig` (an adapter may derive lanes/work-packages from the
 * project's own document structure — e.g. the status-md adapter derives them
 * from STATUS.md sections, so the config never has to mirror the document).
 * Only MISSING/EMPTY fields are filled; anything the config file states wins.
 * Adapters without `suggestedConfig` (npi-docs) leave the config untouched.
 */
function fillFromSuggested(cfg, suggested) {
  if (!suggested) return cfg;
  const out = { ...cfg };
  if (!Array.isArray(out.lanes) || out.lanes.length === 0) out.lanes = suggested.lanes;
  if (!Array.isArray(out.workPackages) || out.workPackages.length === 0)
    out.workPackages = suggested.workPackages;
  if (!out.projectName) out.projectName = suggested.projectName;
  return out;
}

/**
 * @param {string} projectRoot
 * @param {object} adapterData  result of adapter collect() — used for auto-derive
 * @returns {Promise<{config:object, source:string}>}
 */
export async function loadConfig(projectRoot, adapterData) {
  const suggested = adapterData?.suggestedConfig;
  for (const name of CONFIG_NAMES) {
    const p = path.join(projectRoot, name);
    const found = await readJsonIfExists(p);
    if (found) {
      return { config: normalize(fillFromSuggested(found, suggested)), source: name };
    }
  }
  if (suggested) {
    return {
      config: normalize({ ...suggested, _derived: true }),
      source: "auto-derived (adapter)",
    };
  }
  return { config: normalize(deriveConfig(adapterData)), source: "auto-derived" };
}
