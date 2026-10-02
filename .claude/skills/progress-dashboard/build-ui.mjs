// build-ui.mjs — ONE-TIME build: bundle ui-src/ into assets/dashboard.html.
//
// Produces a single self-contained HTML file: React + react-dom + react-markdown
// + remark-gfm + the whole Director Dashboard UI, inlined. No external requests
// (CSP-safe / works offline / works as a Claude Artifact). The runtime data is
// NOT baked in — the file carries a __DASHBOARD_DATA_JSON__ sentinel that
// render.mjs replaces per project.
//
// Run this only when the UI source changes:
//   node build-ui.mjs --deps "<path-to-a-node_modules-with-react+react-markdown>"
//
// esbuild itself and the react/* + react-markdown + remark-gfm packages are
// resolved from the --deps node_modules (defaults to the sibling SaaS project
// that originated this UI). They are BUILD-TIME ONLY — the emitted HTML has no
// dependency on them.

import { readFileSync, writeFileSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));

// --- resolve the build-time node_modules (react etc.) ---------------------
function argVal(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}
const DEFAULT_DEPS = resolve(
  __dirname,
  "../../../../12.subscription-payment-saas-platform/node_modules",
);
const depsDir = resolve(argVal("--deps") ?? DEFAULT_DEPS);
if (!existsSync(join(depsDir, "react"))) {
  console.error(`[build-ui] react not found under ${depsDir}\n` +
    `Pass --deps <node_modules> pointing at an install that has react, ` +
    `react-dom, react-markdown, remark-gfm, and esbuild.`);
  process.exit(2);
}

// esbuild may live in a different node_modules than react (e.g. an isolated
// build install). Defaults to the deps dir.
const esbuildDir = resolve(argVal("--esbuild") ?? depsDir);
const esbuildMain = join(esbuildDir, "esbuild", "lib", "main.js");
if (!existsSync(esbuildMain)) {
  console.error(`[build-ui] esbuild not found at ${esbuildMain}\n` +
    `Pass --esbuild <node_modules> pointing at an install that has esbuild.`);
  process.exit(2);
}
const esbuild = await import(pathToFileURL(esbuildMain).href);

const entry = join(__dirname, "ui-src", "entry.tsx");
const outHtml = join(__dirname, "assets", "dashboard.html");
const outBody = join(__dirname, "assets", "dashboard.body.html");

console.log(`[build-ui] bundling ${entry}`);
const result = await esbuild.build({
  entryPoints: [entry],
  bundle: true,
  format: "iife",
  platform: "browser",
  jsx: "automatic",
  loader: { ".ts": "ts", ".tsx": "tsx" },
  nodePaths: [depsDir],          // resolve bare imports (react, ...) from here
  define: { "process.env.NODE_ENV": '"production"' },
  minify: true,
  write: false,
  legalComments: "none",
  logLevel: "info",
});

const js = result.outputFiles[0].text;

// --- compile Tailwind CSS for the classes used in ui-src -------------------
// The dashboard components style layout with Tailwind utility classes
// (flex/gap/padding/text-sizes/font-mono/...). Those must be compiled and
// inlined, or the standalone bundle renders as unstyled stacked text.
//
// An unstyled bundle is the dangerous outcome here: it is a valid HTML file
// that renders, so nothing downstream errors — it just looks broken, and it
// would be committed as the frozen shell. So the two ways Tailwind can produce
// no utilities are both treated as build failures, not warnings:
//
//   1. tailwindcss is not installed  -> the CLI is missing (checked below)
//   2. tailwindcss runs but produces no utilities -> it exits 0 and emits ~5 KB
//      of preflight-only CSS with zero class selectors. A Tailwind upgrade that
//      changes how `content` is configured (v4 dropped the v3 config contract)
//      lands here, and an exit code of 0 makes it invisible.
//
// Empirically, compiling this UI yields ~116 class selectors; a compile that
// resolved no source files yields exactly 0. The floor below sits far under the
// real count so it never fires on a healthy build, and far over 0 so an empty
// compile can never pass.
const MIN_CLASS_SELECTORS = 20;

function generateTailwindCss() {
  const cliPath = join(depsDir, "tailwindcss", "lib", "cli.js");
  if (!existsSync(cliPath)) {
    console.error(`[build-ui] tailwindcss CLI not found at ${cliPath}\n` +
      `The UI would be emitted unstyled, so the build stops here. ` +
      `Install tailwindcss into the --deps node_modules and re-run.`);
    process.exit(2);
  }
  const srcDir = join(__dirname, "ui-src");
  const tmp = mkdtempSync(join(tmpdir(), "pd-tw-"));
  const posix = (p) => p.replace(/\\/g, "/");
  const inputCss =
    "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n" +
    ":root{color-scheme:light dark}\nhtml,body{height:100%}\n";
  const cfg =
    "module.exports={content:[" +
    JSON.stringify(posix(srcDir) + "/**/*.{ts,tsx}") +
    "],theme:{extend:{}},plugins:[]};\n";
  const inPath = join(tmp, "in.css");
  const cfgPath = join(tmp, "tw.config.cjs");
  const outPath = join(tmp, "out.css");
  writeFileSync(inPath, inputCss, "utf8");
  writeFileSync(cfgPath, cfg, "utf8");
  try {
    execFileSync(process.execPath, [cliPath, "-i", inPath, "-c", cfgPath, "-o", outPath, "--minify"], {
      stdio: ["ignore", "ignore", "inherit"],
      cwd: __dirname,
    });
    const css = readFileSync(outPath, "utf8");
    const selectors = (css.match(/\.[a-zA-Z][a-zA-Z0-9_\\.:[\]%#/-]*\s*[{,]/g) || []).length;
    if (selectors < MIN_CLASS_SELECTORS) {
      console.error(`[build-ui] tailwind produced only ${selectors} class selectors ` +
        `(expected at least ${MIN_CLASS_SELECTORS}).\n` +
        `That is preflight-only CSS — the emitted UI would be unstyled, so the build ` +
        `stops before overwriting assets/.`);
      process.exit(2);
    }
    console.log(`[build-ui] tailwind css: ${(Buffer.byteLength(css, "utf8") / 1024).toFixed(0)} KB ` +
      `(${selectors} class selectors)`);
    return css;
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}
const tailwindCss = generateTailwindCss();

const html = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Progress Dashboard</title>
<style>${tailwindCss}</style>
<style>html,body{margin:0;padding:0;background:#F4F5F7}#root{min-height:100vh}</style>
</head>
<body>
<div id="root"></div>
<script>window.__STATIC__=true;window.__DASHBOARD_DATA__=__DASHBOARD_DATA_JSON__;</script>
<script>${js}</script>
</body>
</html>
`;

writeFileSync(outHtml, html, "utf8");
const kb = (Buffer.byteLength(html, "utf8") / 1024).toFixed(0);
console.log(`[build-ui] wrote ${outHtml} (${kb} KB)`);

// Body-only fragment for the Claude Artifact path (the publisher wraps it in
// its own <!doctype>/<head>/<body>, so we must NOT include those tags here).
const body = `<style>${tailwindCss}</style>
<style>html,body{margin:0;padding:0;background:#F4F5F7}#root{min-height:100vh}</style>
<div id="root"></div>
<script>window.__STATIC__=true;window.__DASHBOARD_DATA__=__DASHBOARD_DATA_JSON__;</script>
<script>${js}</script>
`;
writeFileSync(outBody, body, "utf8");
console.log(`[build-ui] wrote ${outBody} (${(Buffer.byteLength(body, "utf8") / 1024).toFixed(0)} KB)`);
console.log(`[build-ui] data injection sentinel: __DASHBOARD_DATA_JSON__`);
