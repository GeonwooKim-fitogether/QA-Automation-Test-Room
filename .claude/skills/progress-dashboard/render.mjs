// render.mjs — the skill's orchestrator (zero runtime npm deps).
//
//   project canonical sources ──(adapter)──▶ raw signals
//                                            + config (loadConfig)
//                          ──(buildDashboardData)──▶ DashboardData
//                          ──(inject into assets/dashboard.html)──▶ deliverable HTML
//
// Usage:
//   node render.mjs [projectRoot] [--out <file>] [--serve [port]] [--adapter <name>]
//
// Default: reads the current working directory, writes
// <projectRoot>/.claude/progress-dashboard.html, and prints its path + file://
// URL. The agent invoking the skill then publishes that file as an Artifact
// (see SKILL.md). With --serve it instead hosts the dashboard on a local port
// (regenerated on each page load) for cloud port-forwarding.

import fs from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = __dirname;
const TEMPLATE_FULL = path.join(SKILL_DIR, "assets", "dashboard.html");
const TEMPLATE_BODY = path.join(SKILL_DIR, "assets", "dashboard.body.html");
const SENTINEL = "__DASHBOARD_DATA_JSON__";

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") args.out = argv[++i];
    else if (a === "--adapter") args.adapter = argv[++i];
    else if (a === "--serve") {
      args.serve = true;
      const maybePort = argv[i + 1];
      if (maybePort && /^\d+$/.test(maybePort)) args.port = parseInt(argv[++i], 10);
    } else if (a === "--artifact") args.artifact = true;
    else if (!a.startsWith("--")) args._.push(a);
  }
  return args;
}

/**
 * Resolve which adapter to use. Precedence:
 *   1. --adapter flag
 *   2. "adapter" field in the project's dashboard.config.json
 *   3. "npi-docs" (default — unchanged for existing projects)
 */
async function resolveAdapterName(projectRoot, flagValue) {
  if (flagValue) return flagValue;
  for (const name of ["dashboard.config.json", ".claude/dashboard.config.json"]) {
    try {
      const raw = await fs.readFile(path.join(projectRoot, name), "utf-8");
      const cfg = JSON.parse(raw);
      if (cfg && typeof cfg.adapter === "string" && /^[a-z0-9-]+$/i.test(cfg.adapter)) {
        return cfg.adapter;
      }
    } catch {
      // missing/invalid config — fall through
    }
  }
  return "npi-docs";
}

/** Build the full DashboardData for a project (config + adapter + builder). */
async function assemble(projectRoot, adapterName) {
  const adapterMod = await import(
    pathToFileURL(path.join(SKILL_DIR, "core", "adapters", `${adapterName}.mjs`)).href
  );
  const { loadConfig } = await import(pathToFileURL(path.join(SKILL_DIR, "core", "loadConfig.mjs")).href);
  const { buildDashboardData } = await import(
    pathToFileURL(path.join(SKILL_DIR, "core", "buildDashboardData.mjs")).href
  );

  const adapterData = await adapterMod.collect(projectRoot);
  const { config, source: configSource } = await loadConfig(projectRoot, adapterData);

  const base = buildDashboardData({ config, ...adapterData });

  // Mirror loadState.ts's final enrichment so the UI gets docs/worklists/
  // gitActivity/decisions exactly as it expects.
  const data = {
    ...base,
    docs: adapterData.docs ?? [],
    worklists: adapterData.worklists ?? {},
    gitActivity: adapterData.gitActivity ?? undefined,
    decisions: (adapterData.decisions ?? []).map((d) => ({
      id: d.id, title: d.title, date: d.date, verdict: d.verdict, wps: d.wps, closes: d.closes,
    })),
    source: {
      ...base.source,
      // Adapters that read a different canonical source may say so.
      file: adapterData.sourceFile ?? base.source.file,
      generatedAt: new Date().toISOString(),
      configSource,
      adapter: adapterName,
    },
  };
  return { data, configSource, configDerived: config._derived };
}

/** Inject the data JSON into the bundled template, returning final HTML. */
async function renderHtml(data, templatePath) {
  const tmpl = await fs.readFile(templatePath, "utf-8");
  if (!tmpl.includes(SENTINEL)) {
    throw new Error(`template ${templatePath} is missing the ${SENTINEL} sentinel — rebuild with build-ui.mjs`);
  }
  // Escape "<" so a stray "</script>" inside data can't break out of the tag.
  const json = JSON.stringify(data).replace(/</g, "\\u003c");
  return tmpl.replace(SENTINEL, json);
}

async function main() {
  const args = parseArgs(process.argv);
  const projectRoot = path.resolve(args._[0] ?? process.cwd());
  const adapterName = await resolveAdapterName(projectRoot, args.adapter);

  const templatePath = args.artifact ? TEMPLATE_BODY : TEMPLATE_FULL;
  try {
    await fs.access(templatePath);
  } catch {
    console.error(`[render] missing ${templatePath}. Run build-ui.mjs first.`);
    process.exit(2);
  }

  if (args.serve) {
    const port = args.port ?? 4173;
    const server = http.createServer(async (req, res) => {
      if (req.url === "/favicon.ico") { res.statusCode = 204; res.end(); return; }
      try {
        const { data } = await assemble(projectRoot, adapterName);
        const html = await renderHtml(data, TEMPLATE_FULL);
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.end(html);
      } catch (err) {
        res.statusCode = 500;
        res.end(`render error: ${err?.stack || err}`);
        console.error("[render] error:", err);
      }
    });
    server.listen(port, () => {
      console.log(`[render] serving ${projectRoot}`);
      console.log(`[render] → http://localhost:${port}  (refresh = latest snapshot)`);
    });
    return;
  }

  const { data, configSource, configDerived } = await assemble(projectRoot, adapterName);
  const html = await renderHtml(data, templatePath);
  const defaultOut = args.artifact ? "progress-dashboard.body.html" : "progress-dashboard.html";
  const outPath = path.resolve(args.out ?? path.join(projectRoot, ".claude", defaultOut));
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  await fs.writeFile(outPath, html, "utf-8");

  const kb = (Buffer.byteLength(html, "utf-8") / 1024).toFixed(0);
  console.log(`[render] project:  ${projectRoot}`);
  console.log(`[render] config:   ${configSource}${configDerived ? " (auto-derived — add dashboard.config.json for full labels)" : ""}`);
  console.log(`[render] tickets:  ${data.tickets.length} · lanes: ${data.lanes.length}`);
  console.log(`[render] wrote:    ${outPath} (${kb} KB)`);
  console.log(`[render] open:     ${pathToFileURL(outPath).href}`);
}

main().catch((err) => {
  console.error("[render] fatal:", err?.stack || err);
  process.exit(1);
});
