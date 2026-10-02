// buildDashboardData.mjs — config-driven port of the SaaS dashboard's
// buildDashboardData.ts. The assembly LOGIC is identical (stuck-centered
// column derivation, closure/progress precedence, link inference, 4-chip
// summary). The only change: the structural constants that were hardcoded
// module consts (PROJECT_NAME, STATIC_LANES, PHASE_WPS, IPS,
// ARCHITECTURAL_HCPS, friendly-vocabulary) now come from `config` (see
// loadConfig.mjs). That is what makes the same UI render any project.

import { friendlyHcp as lexiconFriendlyHcp } from "./directorLexicon.mjs";

// --- small helpers ---------------------------------------------------------

function safeId(...parts) {
  const joined = parts.filter(Boolean).join("-").toLowerCase();
  return joined.replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "") || "ticket";
}

function shortPhrase(s, max = 56) {
  if (!s) return "—";
  const cleaned = s.replace(/\s+/g, " ").trim();
  if (cleaned.length <= max) return cleaned;
  return cleaned.slice(0, max - 1) + "…";
}

/** Config vocabulary wins; fall back to the bundled lexicon. */
function friendly(code, cfg) {
  if (cfg.vocabulary && cfg.vocabulary[code]) return cfg.vocabulary[code];
  return lexiconFriendlyHcp(code);
}

// --- per-card highlights (column-independent) ------------------------------

function deriveHighlights(column, ticketType, closure, progress, worklist) {
  if (closure?.verdict === "closed" || closure?.verdict === "rejected") return [];
  const out = [];
  if (column === "Verification") out.push("blocked");
  if (column === "Plan" && worklist && worklist.tasks.length > 0) out.push("active");
  if (column === "Development" && progress && progress.done > 0) out.push("active");
  if ((ticketType === "HCP Gate" || ticketType === "Queue") && !out.includes("blocked")) {
    out.push("blocked");
  }
  return out;
}

// --- 1) Phase/WP tickets (stuck-centered) ----------------------------------

function buildWpTickets(cfg, worklists, progress, closure, reReview, oneLiners) {
  return cfg.workPackages.map((p) => {
    const codes = [p.wp, p.legacy].filter(Boolean);
    const c = codes.map((k) => closure[k]).find(Boolean);
    const prog = codes.map((k) => progress[k]).find(Boolean);
    const worklist = codes.map((k) => worklists[k]).find(Boolean);
    const flaggedForReReview = codes.some((k) => reReview.has(k));
    const plainTitle = oneLiners[p.wp] ?? oneLiners[p.legacy] ?? p.plainTitle ?? p.title;

    let column = "Backlog";
    let tone = "backlog";
    let gateType;
    let nextAction;

    if (c?.verdict === "closed" && flaggedForReReview) {
      column = "Review"; tone = "review";
      nextAction = "🔄 Director가 기획 의도 재검토 요청";
    } else if (c?.verdict === "closed") {
      column = "Done"; tone = "done";
      nextAction = c.date ? `완료됨 — ${c.decisionId} (${c.date})` : `완료됨 — ${c.decisionId}`;
    } else if (c?.verdict === "rejected") {
      column = "Backlog"; tone = "hcp";
      nextAction = `반려됨 — ${c.decisionId}`;
    } else if (prog && prog.done >= prog.total) {
      column = "Verification"; tone = "review";
      gateType = "Final";
      nextAction = "→ Director가 최종 수락 검토 필요";
    } else if (prog && prog.done > 0) {
      column = "Development"; tone = "progress";
      nextAction = `→ AI 구현 중 — ${prog.done}/${prog.total}`;
    } else if (worklist && worklist.tasks.length > 0) {
      column = "Plan"; tone = "progress";
      nextAction = "→ AI 곧 시작 (기획 통과, 구현 대기)";
    } else {
      nextAction = "→ 아직 계획 안 됨";
    }

    // Two progress figures can appear for the same card and they are derived
    // differently, so they legitimately disagree: the card badge counts git
    // commits (or the state sentinel), while this one counts the completed rows
    // of the worklist document. Each is labelled with where it came from —
    // an unlabelled pair of different numbers just makes the reader guess which
    // one to believe. (`lastSha` is guarded because it is often an empty string,
    // which used to render as a dangling "Last commit:" with nothing after it.)
    const description = [
      c?.date && c?.decisionId ? `${c.decisionId} · ${c.date}` : c?.decisionId,
      worklist
        ? `worklist 문서 기준 ${worklist.totals.done}/${worklist.totals.total} done`
        : undefined,
      prog
        ? `카드 badge 는 ${prog.source === "sentinel" ? "state 문구" : "git 커밋"} 기준 ${prog.done}/${prog.total}`
        : undefined,
      prog?.lastSha ? `last commit ${prog.lastSha}` : undefined,
    ].filter(Boolean).join(" · ") || undefined;

    const codeLabel = p.legacy && p.legacy !== p.wp ? `${p.legacy} / ${p.wp}` : (p.wp || p.legacy);

    const ticket = {
      id: safeId(p.wp || p.legacy, "wp"),
      code: codeLabel,
      title: p.title,
      plainTitle,
      nextAction,
      gateType,
      lane: p.laneId,
      column,
      type: "F-item",
      tone,
      actor: cfg.roles.implementer,
      links: [],
      description,
      progress: prog && prog.done > 0
        ? {
            done: prog.done,
            total: prog.total,
            lastSha: prog.lastSha || undefined,
            raw: prog.raw,
            source: "commits",
            commitCount: prog.commitCount,
          }
        : undefined,
      closure: c
        ? { verdict: c.verdict, decisionId: c.decisionId, date: c.date, title: c.title }
        : undefined,
    };
    const h = deriveHighlights(column, "F-item", c, prog, worklist);
    if (h.length > 0) ticket.highlights = h;
    return ticket;
  });
}

// --- 2) Meta items + meta sprint -------------------------------------------

function buildMetaTickets(cfg, oneLiners) {
  const out = [];
  const ms = cfg.metaSprint;
  if (ms) {
    out.push({
      id: ms.id || "metasprint-1",
      code: ms.code || "MetaSprint-1",
      title: ms.title || "Meta Sprint",
      plainTitle: oneLiners[(ms.code || "").toUpperCase().replace(/[^A-Z0-9]/g, "")] ?? ms.plainTitle ?? ms.title,
      nextAction: ms.nextAction || "→ 진행 중",
      lane: ms.laneId || "meta",
      column: ms.column || "Development",
      type: "Meta",
      tone: "meta",
      actor: cfg.roles.system,
      description: ms.description,
    });
  }
  for (const m of cfg.metaItems) {
    out.push({
      id: safeId(m.ip || m.legacy, "ip"),
      code: m.legacy && m.legacy !== m.ip ? `${m.ip} (${m.legacy})` : (m.ip || m.legacy),
      title: m.title,
      plainTitle: oneLiners[m.ip] ?? oneLiners[m.legacy] ?? m.plainTitle ?? m.title,
      nextAction: m.nextAction || "→ 후보 — 아직 구현 시작 안 됨",
      lane: m.laneId || "meta",
      column: "Backlog",
      type: "Meta",
      tone: "meta",
      actor: cfg.roles.system,
      description: m.description || "candidate only — implementation NOT started.",
    });
  }
  return out;
}

// --- 3) Decision-log records -----------------------------------------------

function buildDecisionRecords(cfg, decisions, controlLaneId) {
  return decisions.map((d) => ({
    id: safeId(d.id, "rec"),
    code: d.id,
    title: shortPhrase(d.title, 80),
    nextAction: d.date
      ? `박제됨 — ${d.date}${d.closes ? " · WP 종결" : ""}`
      : `박제됨${d.closes ? " · WP 종결" : ""}`,
    lane: controlLaneId,
    column: "Done",
    type: "Record",
    tone: d.verdict === "rejected" ? "hcp" : "done",
    actor: cfg.roles.director,
    description: [
      d.date,
      d.verdict !== "neutral" ? d.verdict : undefined,
      d.closes ? "closes WP" : undefined,
    ].filter(Boolean).join(" · ") || undefined,
  }));
}

// --- 4) Pending DQ queue ----------------------------------------------------

function buildQueueTickets(cfg, queue, controlLaneId) {
  return queue.map((q) => {
    const ticketType = q.isHcp ? "HCP Gate" : "Queue";
    const gateType = q.isHcp ? "HCP" : "Plan";
    const plainTitle = q.isHcp && q.hcpCode ? friendly(q.hcpCode, cfg) : shortPhrase(q.decisionNeeded, 50);
    const nextAction = q.isHcp ? "→ Director가 보안 결정 필요" : "→ Director가 기획 결정 필요";
    return {
      id: safeId(q.id, "queue"),
      code: q.hcpCode ?? q.id,
      title: shortPhrase(q.decisionNeeded, 80),
      plainTitle,
      nextAction,
      gateType,
      lane: controlLaneId,
      column: q.isHcp ? "Verification" : "Plan",
      type: ticketType,
      tone: "hcp",
      actor: cfg.roles.director,
      description: [
        q.id,
        q.reviewTiming ? `review: ${q.reviewTiming}` : undefined,
        q.risk ? `risk: ${q.risk}` : undefined,
        shortPhrase(q.defaultAction, 160),
      ].filter(Boolean).join(" · "),
      highlights: ["blocked"],
    };
  });
}

// --- 5) Architectural HCP boundaries ---------------------------------------

function buildArchitecturalHcps(cfg, controlLaneId) {
  return cfg.architecturalHcps.map((h) => ({
    id: safeId(h.code, "arch-hcp"),
    code: h.code,
    title: h.title,
    plainTitle: friendly(h.code, cfg),
    nextAction: h.status === "out-of-cycle"
      ? "→ 본 cycle 외 — 발생 시 cycle 중단 + escalation"
      : `→ 미래 — trigger 발생 시 별도 결정 (${h.anchor})`,
    lane: controlLaneId,
    column: "Backlog",
    type: "HCP Gate",
    tone: "hcp",
    actor: cfg.roles.director,
    description: `[${h.status}] anchor: ${h.anchor} · ${h.reason}`,
  }));
}

// --- cross-ticket link inference -------------------------------------------

function inferLinks(tickets) {
  function codeTokens(t) {
    const re = /(F-?\d{2,3}|WP-?\d{3}|IP-?\d{3}|M-?\d{3}|D-?\d{3}|DQ-?\d{3}|T-?\d{1,2}|HCP-[A-Z-]+)/gi;
    const all = `${t.code} ${t.title} ${t.description ?? ""}`;
    return Array.from(new Set((all.match(re) ?? []).map((s) => s.toUpperCase().replace(/-/g, ""))));
  }
  const tokenIndex = new Map();
  for (const t of tickets) {
    for (const tok of codeTokens(t)) {
      if (!tokenIndex.has(tok)) tokenIndex.set(tok, new Set());
      tokenIndex.get(tok).add(t.id);
    }
  }
  return tickets.map((t) => {
    const links = new Set(t.links ?? []);
    for (const tok of codeTokens(t)) {
      const peers = tokenIndex.get(tok);
      if (!peers) continue;
      for (const peer of peers) if (peer !== t.id) links.add(peer);
    }
    return { ...t, links: Array.from(links) };
  });
}

// --- tree builder (generalized over config lanes) --------------------------

function phaseStatusLabel(tickets, id, base) {
  const inLane = tickets.filter((t) => t.lane === id && t.type === "F-item");
  if (inLane.length === 0) return `${base} · Future`;
  if (inLane.some((t) => t.column === "Review")) return `${base} · RE-REVIEW`;
  if (inLane.every((t) => t.column === "Done")) return `${base} · CLOSED`;
  if (inLane.some((t) => t.column === "Verification")) return `${base} · DIRECTOR`;
  if (inLane.some((t) => t.column === "Development" || t.column === "Plan")) return `${base} · ACTIVE`;
  return base;
}

function buildTree(cfg, tickets) {
  function children(laneId) {
    const isProduct = (cfg.lanes.find((l) => l.id === laneId)?.parent) === "Product";
    const allowedTypes = isProduct
      ? ["F-item", "Gate", "HCP Gate", "Task"]
      : ["F-item", "Gate", "HCP Gate", "Task", "Meta", "Record", "Queue"];
    return tickets
      .filter((t) => t.lane === laneId)
      .filter((t) => allowedTypes.includes(t.type))
      .slice(0, 12)
      .map((t) => ({ id: `tree-${t.id}`, label: t.code, kind: "ticket", ticketId: t.id }));
  }

  const productLanes = cfg.lanes.filter((l) => l.parent === "Product");
  const metaLanes = cfg.lanes.filter((l) => l.parent === "Meta");
  const controlLanes = cfg.lanes.filter((l) => l.parent === "Control");

  const rootChildren = [];

  if (productLanes.length > 0) {
    rootChildren.push({
      id: "product",
      label: "Product",
      kind: "group",
      children: productLanes.map((l) => ({
        id: l.id,
        label: phaseStatusLabel(tickets, l.id, l.label),
        kind: "lane",
        laneId: l.id,
        children: children(l.id),
      })),
    });
  }

  for (const l of metaLanes) {
    rootChildren.push({
      id: l.id,
      label: l.label,
      kind: "lane",
      laneId: l.id,
      children: children(l.id),
    });
  }

  for (const l of controlLanes) {
    rootChildren.push({
      id: l.id,
      label: l.label,
      kind: "lane",
      laneId: l.id,
      children: [
        { id: `${l.id}-queue`, label: "HCP Queue", kind: "queue", laneId: l.id, typeFilter: "Queue" },
        { id: `${l.id}-log`, label: "Decision Log", kind: "queue", laneId: l.id, typeFilter: "Record" },
      ],
    });
  }

  return {
    id: "project",
    label: `Project — ${cfg.projectName}`,
    kind: "root",
    children: rootChildren,
  };
}

// --- summary (4-chip Director-first) ---------------------------------------

function buildSummary(cfg, tickets) {
  const productLaneIds = cfg.lanes.filter((l) => l.parent === "Product").map((l) => l.id);
  const laneIndex = new Map(productLaneIds.map((id, i) => [id, i + 1]));

  let lastShipped;
  for (const lane of productLaneIds) {
    const done = tickets.find((t) => t.lane === lane && t.type === "F-item" && t.column === "Done");
    if (done) lastShipped = done;
  }

  const now =
    tickets.find((t) => t.type === "F-item" && t.column === "Verification") ??
    tickets.find((t) => t.type === "F-item" && t.column === "Development") ??
    tickets.find((t) => t.type === "F-item" && t.column === "Plan");

  const awaiting =
    tickets.find((t) => t.type === "HCP Gate" && t.column === "Verification") ??
    tickets.find((t) => t.type === "Queue" && t.column === "Plan") ??
    tickets.find((t) => t.type === "HCP Gate" && t.column === "Backlog");

  const blockersCount = tickets.filter(
    (t) =>
      (t.type === "HCP Gate" || t.type === "Queue" || t.type === "F-item") &&
      (t.column === "Verification" || t.column === "Plan"),
  ).length;

  const stripF = (s) => s.replace(/^F-\d{3}\s*\/\s*/, "");

  const lastShippedValue = lastShipped
    ? `${stripF(lastShipped.code)} ${shortPhrase(lastShipped.title, 24)}`.trim()
    : "—";
  const lastShippedSub = lastShipped?.closure?.decisionId;

  const nowPhaseNum = now ? laneIndex.get(now.lane) : undefined;
  const nowValue = now
    ? (nowPhaseNum ? `Phase ${nowPhaseNum} · ${stripF(now.code)}` : stripF(now.code))
    : "(idle)";
  const nowSub = now
    ? now.progress
      ? `T-${now.progress.done}/${now.progress.total}${now.progress.lastSha ? ` · ${now.progress.lastSha}` : ""}`
      : shortPhrase(now.title, 28)
    : "Director-self-paced selection";

  const awaitingValue = awaiting ? friendly(awaiting.code, cfg) : "차단 없음";
  const awaitingSub = awaiting ? `${awaiting.code} · ${awaiting.actor ?? "Director"}` : undefined;

  const blockersValue = blockersCount === 0 ? "0" : `${blockersCount} open`;
  const blockersSub = blockersCount === 0 ? "no immediate block" : undefined;

  const display = [
    { key: "lastShipped", label: "Last shipped", value: lastShippedValue, sub: lastShippedSub, tone: lastShipped ? "green" : "neutral" },
    { key: "now",         label: "Now",          value: nowValue,         sub: nowSub,         tone: now ? "blue" : "neutral" },
    { key: "awaiting",    label: "Awaiting you", value: awaitingValue,    sub: awaitingSub,    tone: awaiting ? "amber" : "neutral" },
    { key: "blockers",    label: "Blockers",     value: blockersValue,    sub: blockersSub,    tone: blockersCount > 0 ? "red" : "neutral" },
  ];

  return {
    projectName: cfg.projectName,
    chips: {
      foundation: lastShipped ? `${lastShipped.code.split("/").pop()?.trim() ?? ""} ✓` : "—",
      gate: awaiting ? shortPhrase(awaiting.code, 28) : "—",
      next: now ? (now.code.split("/").pop()?.trim() ?? "—") : "—",
      hcp: blockersCount,
    },
    display,
  };
}

// --- public entry ----------------------------------------------------------

/**
 * @param {object} input  { config, decisions, decisionQueue, worklists,
 *                          progress, closure, reReview?, specOneLiners? }
 * @returns {object} DashboardData (lanes, tickets, tree, summary, source)
 */
export function buildDashboardData(input) {
  const cfg = input.config;
  const reReview = input.reReview ?? new Set();
  const oneLiners = input.specOneLiners ?? {};
  const controlLane = cfg.lanes.find((l) => l.parent === "Control");
  const controlLaneId = controlLane ? controlLane.id : "control";

  const wpTickets = buildWpTickets(cfg, input.worklists, input.progress, input.closure, reReview, oneLiners);
  const metaTickets = buildMetaTickets(cfg, oneLiners);
  const decisionRecords = buildDecisionRecords(cfg, input.decisions, controlLaneId);
  const queueTickets = buildQueueTickets(cfg, input.decisionQueue, controlLaneId);
  const archHcps = buildArchitecturalHcps(cfg, controlLaneId);

  const wpTicketsStamped = wpTickets.map((t) => {
    const wp = t.code.match(/WP-\d{3}/i)?.[0]?.toUpperCase();
    const f = t.code.match(/F-\d{3}/i)?.[0]?.toUpperCase();
    const flagged = (wp && reReview.has(wp)) || (f && reReview.has(f));
    return flagged ? { ...t, reviewFlag: true } : t;
  });

  const ticketsRaw = [
    ...wpTicketsStamped,
    ...metaTickets,
    ...queueTickets,
    ...archHcps,
    ...decisionRecords,
  ];

  const ticketsWithLinks = inferLinks(ticketsRaw);
  const tree = buildTree(cfg, ticketsWithLinks);
  const summary = buildSummary(cfg, ticketsWithLinks);

  // Agent-view swimlane metadata. When the project defines its own actors,
  // emit them (order + subtitles). Otherwise leave undefined so the UI uses
  // its legacy four-actor fallback (Director / Claude Code / GPT / System).
  const agents = cfg.actors.length > 0
    ? cfg.actors.map((a) => ({ label: a.label, outcome: a.outcome }))
    : undefined;

  return {
    lanes: cfg.lanes,
    tickets: ticketsWithLinks,
    tree,
    summary,
    agents,
    source: {
      file: "meta/decisions.md + meta/decision-queue.md + worklists + git log",
      schemaVersion: "skill-v1-config-driven",
    },
  };
}
