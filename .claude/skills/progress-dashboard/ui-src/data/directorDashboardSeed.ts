// Static seed data for the Director Dashboard MVP prototype.
// SYNCED to Operating Model v0.3.1 state as of 2026-05-13 (mirrors dashboard/dashboard-state.json schema_version 1.2.0).
//
// Sources of truth: ai-npi/OPERATING_MODEL.md · STATUS.md · dashboard/dashboard-state.json.
// This file is a snapshot — manual sync on each phase / gate / D-NNN change.
//
// No backend fetch. No DB. No payment integration. No secrets.

import type { Lane, SummaryPayload, Ticket, TreeNode } from "../types";

export const LANES: Lane[] = [
  { id: "phase1", label: "Phase 1 — Foundation / Auth · CLOSED",                parent: "Product" },
  { id: "phase2", label: "Phase 2 — Subscription Domain · CLOSED",              parent: "Product" },
  { id: "phase3", label: "Phase 3 — TossPayments Integration · PLANNING",       parent: "Product" },
  { id: "phase4", label: "Phase 4 — BillingCycle · Not Started",                parent: "Product" },
  { id: "phase5", label: "Phase 5 — Invoice · Not Started",                     parent: "Product" },
  { id: "meta",   label: "Meta Sprint 1 — Agent Operating System · ACTIVE",     parent: "Meta"    },
  { id: "control",label: "Control Plane — Records & Queues",                    parent: "Control" },
];

export const TICKETS: Ticket[] = [
  // ============================================================
  // Phase 1 — Foundation / Auth · CLOSED (merge 3b8bc60)
  // ============================================================
  {
    id: "f001",
    code: "F-001 / WP-001",
    title: "Supabase Auth",
    lane: "phase1",
    column: "Done",
    type: "F-item",
    tone: "done",
    actor: "Claude Code",
    risk: "low",
    links: ["g001", "r001", "c001-d008"],
    description:
      "Phase 1 product work package. Supabase sign-up · login · session refresh · protected route · logout. " +
      "Closed conditionally complete (learning cycle) per D-008. AC-A 7/7 PASS · AC-B §A.2.R REVISED (D-009).",
    subtasks: [
      { id: "f001-s1", label: "AC-A offline scaffold 7/7 PASS",                done: true },
      { id: "f001-s2", label: "AC-B §A.2.R Implementer-driven validation",     done: true },
      { id: "f001-s3", label: "H-001 hotfix (useActionState → useFormState)",  done: true },
      { id: "f001-s4", label: "PR #1 squash → main 3b8bc60",                   done: true },
      { id: "f001-s5", label: "Director Final CP (D-008)",                     done: true },
    ],
  },

  // ============================================================
  // Phase 2 — Subscription Domain · CLOSED (merge 9b2967a, D-013)
  // ============================================================
  {
    id: "f002",
    code: "F-002 / WP-002",
    title: "Subscription Core (mock-payment)",
    lane: "phase2",
    column: "Done",
    type: "F-item",
    tone: "done",
    actor: "Claude Code",
    risk: "med",
    links: ["g001", "g002", "f001", "c001-d013", "r002"],
    description:
      "Phase 2 product work package — Subscription Domain. Closed by Director D-013 (2026-05-12). " +
      "AC-A 11/11 + AC-B 10/10 PASS (env gap accepted as known limitation, D-007/§A.2.R pattern). " +
      "7 modules + 8-state machine + 4 RLS + 26 tasks (T-1~T-26).",
    subtasks: [
      { id: "f002-s1", label: "AC-A 11/11 PASS (offline scaffold)",            done: true },
      { id: "f002-s2", label: "AC-B 10/10 PASS (integration, env gap accepted)", done: true },
      { id: "f002-s3", label: "52/52 unit + integration tests PASS",           done: true },
      { id: "f002-s4", label: "T-17 DB migration (Director self-execute)",     done: true },
      { id: "f002-s5", label: "Director Final CP Accept (D-013)",              done: true },
      { id: "f002-s6", label: "PR #12 squash → main 9b2967a",                  done: true },
    ],
  },
  {
    id: "g001",
    code: "G-001",
    title: "Phase 2 Entry Gate (Start Phase 2?)",
    lane: "phase2",
    column: "Done",
    type: "Gate",
    tone: "done",
    actor: "Director",
    risk: "low",
    links: ["f001", "f002"],
    description:
      "Phase 2 entry decision. APPROVED with 8 explicit constraints (D-010, 2026-05-11): " +
      "dev/test only · mock data only · reversible migration only · no production data / deployment / architecture · " +
      "no service_role / DB password / JWT secret · schema traceable. Cycle separation (F-003 / F-004 / F-005 deferred).",
  },
  {
    id: "g002",
    code: "G-002",
    title: "DB Schema Migration (T-17)",
    lane: "phase2",
    column: "Done",
    type: "HCP Gate",
    tone: "done",
    actor: "Director",
    risk: "med",
    links: ["f002", "c001-d013"],
    description:
      "HCP gate — Director self-execute migration via Supabase dashboard SQL editor (T-17 pattern). " +
      "PASS 2026-05-12. Reversible (column drop). Implementer auto-validate 3/3 PASS. " +
      "Boundary preserved (no service_role usage by Implementer).",
  },

  // ============================================================
  // Phase 3 — TossPayments Integration · PLANNING ACCEPTED (D-015)
  // Implementation NOT entered. Next gate = T-4 HCP-PAYMENT-SANDBOX-KEY.
  // ============================================================
  {
    id: "f003",
    code: "F-003 / WP-003",
    title: "TossPayments Integration (sandbox-only first)",
    lane: "phase3",
    column: "Development",
    type: "F-item",
    tone: "selected",
    actor: "Claude Code",
    risk: "high",
    links: ["g003", "g004", "t-webhook", "t-payments-table", "c001-d015"],
    description:
      "Phase 3 product work package. Planning Final CP Accepted (D-015, 2026-05-13, PR #14 squash b9d9f00). " +
      "Spec + Design + Worklist + FIL-009 박제. DD-3 defaults approved (mock adapter default · webhook stub Option C · payments-table defer to WP-005). " +
      "Implementation 미진입 — T-4 HCP-PAYMENT-SANDBOX-KEY 자율 시점 대기.",
    subtasks: [
      { id: "f003-s1", label: "AC-A planning 10/10 PASS",                      done: true },
      { id: "f003-s2", label: "Spec / Design / Worklist 박제 (3 docs)",         done: true },
      { id: "f003-s3", label: "FIL-009 HCP sub-category pattern 박제",          done: true },
      { id: "f003-s4", label: "T-4 sandbox key (Director self-paced)",         done: false },
      { id: "f003-s5", label: "T-5~T-22 implementation (post-T-4)",            done: false },
      { id: "f003-s6", label: "T-23 DB schema migration (HCP)",                done: false },
      { id: "f003-s7", label: "AC-B 11/11 integration validation",             done: false },
    ],
  },
  {
    id: "g003",
    code: "T-4 / HCP-PAYMENT-SANDBOX-KEY",
    title: "TossPayments Sandbox Key Entry Gate",
    lane: "phase3",
    column: "Verification",
    type: "HCP Gate",
    tone: "hcp",
    actor: "Director",
    risk: "high",
    links: ["f003", "h001"],
    description:
      "Open HCP. WP-003 implementation entry gate (Director self-paced). " +
      "TossPayments test merchant signup + sandbox key issuance + .env.local introduction approval. " +
      "Boundary = sandbox-only, no production credentials. Worklist 정의상 T-5+ 모두 본 gate dependency.",
    subtasks: [
      { id: "g003-s1", label: "Director TossPayments test merchant signup",    done: false },
      { id: "g003-s2", label: "Sandbox key issuance",                          done: false },
      { id: "g003-s3", label: ".env.local introduction (anon scope)",          done: false },
    ],
  },
  {
    id: "g004",
    code: "T-23 / HCP-DB-SCHEMA-EXEC-WP003",
    title: "subscriptions ALTER (2 nullable cols)",
    lane: "phase3",
    column: "Backlog",
    type: "HCP Gate",
    tone: "hcp",
    actor: "Director",
    risk: "med",
    links: ["f003", "h002"],
    description:
      "Future HCP. Director self-execute via Supabase dashboard SQL editor (WP-002 T-17 pattern). " +
      "2 nullable columns: toss_billing_key_ref · last_payment_key. Reversible (column drop). " +
      "Triggers only after T-15 offline migration SQL drafted + T-22 security grep PASS.",
  },
  {
    id: "t-webhook",
    code: "WP-003 / Webhook Stub",
    title: "Webhook Inclusion (Option C default)",
    lane: "phase3",
    column: "Backlog",
    type: "Task",
    tone: "backlog",
    actor: "Claude Code",
    risk: "low",
    links: ["f003", "h003"],
    description:
      "Webhook stub only — no real wiring (D-015 default Option C). " +
      "Real webhook URL registration would trigger HCP-WEBHOOK (separate Director decision).",
  },
  {
    id: "t-payments-table",
    code: "WP-003 / Payments Table",
    title: "Payments Table (proposal only, defer to WP-005)",
    lane: "phase3",
    column: "Backlog",
    type: "Task",
    tone: "backlog",
    actor: "Claude Code",
    risk: "low",
    links: ["f003", "f005"],
    description:
      "PAYMENTS-TABLE deferred to WP-005 per D-015 default. " +
      "Current WP-003 cycle = subscriptions ALTER 2 nullable cols only. " +
      "Full payments table design = WP-005 (Invoice / Statement) scope.",
  },

  // ============================================================
  // Phase 4 — BillingCycle · Not Started
  // ============================================================
  {
    id: "f004",
    code: "F-004 / WP-004",
    title: "BillingCycle / CronJob",
    lane: "phase4",
    column: "Backlog",
    type: "F-item",
    tone: "backlog",
    risk: "med",
    description:
      "Future Product Track work package. Recurring billing cycle + idempotent CronJob. " +
      "Entry only after Phase 3 cycle closure. No planning artifacts yet.",
  },

  // ============================================================
  // Phase 5 — Invoice · Not Started
  // ============================================================
  {
    id: "f005",
    code: "F-005 / WP-005",
    title: "Invoice / Statement",
    lane: "phase5",
    column: "Backlog",
    type: "F-item",
    tone: "backlog",
    risk: "low",
    description:
      "Future Product Track work package. Invoice issuance + statement history. " +
      "Includes deferred PAYMENTS-TABLE design (D-015 deferral). Entry after Phase 4 cycle closure.",
  },

  // ============================================================
  // Meta Sprint 1 — Agent Operating System · ACTIVE (planning only, D-014)
  // All 3 IPs Spec 박제 완료, 구현 0 (Stage 3 calibration).
  // ============================================================
  {
    id: "m001",
    code: "IP-001 (M-001)",
    title: "Agent-to-Agent Orchestration / HCP Notification",
    lane: "meta",
    column: "Backlog",
    type: "Meta",
    tone: "meta",
    actor: "System",
    risk: "low",
    links: ["h001", "c001-d014"],
    description:
      "Candidate only — implementation NOT started (Stage 3 calibration constraint). " +
      "Roadmap: v0.1 dry-run · v0.2 Discord · v0.3 persistent runner · v0.4 multi-channel (Discord → KakaoTalk). " +
      "Stage 3 calibration (D-011/D-013/D-014): 복붙 제거 + Director View 보존까지. 의사결정 자동화 금지.",
  },
  {
    id: "m002",
    code: "IP-002 (M-002)",
    title: "Supabase Key Format Drift Handling",
    lane: "meta",
    column: "Backlog",
    type: "Meta",
    tone: "meta",
    actor: "System",
    risk: "low",
    description:
      "Candidate only. Supabase legacy JWT (NEXT_PUBLIC_SUPABASE_ANON_KEY) vs new sb_publishable_* key format dual support. " +
      "구현 검토 시점 = 다음 Supabase 의존 cycle (WP-003 / WP-004 / WP-005).",
  },
  {
    id: "m003",
    code: "IP-003 (M-003)",
    title: "Feature Visibility Gap (e2e auto-validation)",
    lane: "meta",
    column: "Backlog",
    type: "Meta",
    tone: "meta",
    actor: "System",
    risk: "med",
    description:
      "Candidate only. e2e auto-validation 도구 (Chrome MCP / Playwright / 동등) 도입 feasibility study. " +
      "Trigger = 2nd 회귀 발생 또는 명시적 도구 선택.",
  },

  // ============================================================
  // Control Plane — Decision Log + HCP Queue + Records
  // ============================================================

  // ---- Decision Log (3 most recent decisions) ----
  {
    id: "c001-d013",
    code: "D-013",
    title: "F-002 Final CP Accept · Phase 2 CLOSED · v0.3 frozen",
    lane: "control",
    column: "Done",
    type: "Record",
    tone: "done",
    actor: "Director",
    links: ["f002", "g002"],
    description:
      "2026-05-12. Phase 2 closed. AC-A 11/11 + AC-B 10/10 PASS. " +
      "Operating Model v0.3 frozen (Reduced-copy Stage 2 + Calibrated Stage 3 + Naming Model). " +
      "PR #12 squash → main 7a4fe2c.",
  },
  {
    id: "c001-d014",
    code: "D-014",
    title: "v0.3.1 Naming Patch · Meta Sprint 1 ACTIVE (planning only)",
    lane: "control",
    column: "Done",
    type: "Record",
    tone: "done",
    actor: "Director",
    links: ["m001", "m002", "m003"],
    description:
      "2026-05-12. (b)+(c) parallel: Meta Sprint 1 entry (Stage 2 reduced-copy first cycle) + Foundry v0.2.1 frozen 격상 별도 PR. " +
      "§12.6 Document Terminology Map (Brief→Spec / Blueprint→Design) · §12.7 File Rename Map · §12.8 Meta Sprint planning entry pattern.",
  },
  {
    id: "c001-d015",
    code: "D-015",
    title: "WP-003 Planning Final CP Accept · Phase 3 PLANNING",
    lane: "control",
    column: "Done",
    type: "Record",
    tone: "done",
    actor: "Director",
    links: ["f003"],
    description:
      "2026-05-13. Spec + Design + Worklist + FIL-009 채택. DD-3 defaults approved (ADAPTER-SELECTOR default mock + production 강제 · WEBHOOK Option C stub · PAYMENTS-TABLE defer to WP-005). " +
      "HCP-PAYMENT 4 sub-category 분리 (SANDBOX-KEY / PROD / LIVE / WEBHOOK). PR #14 squash b9d9f00. Implementation 미진입.",
  },

  // ---- HCP Queue (1 open · 2 future) ----
  {
    id: "h001",
    code: "HCP-PAYMENT-SANDBOX-KEY",
    title: "WP-003 T-4 Entry Gate · OPEN",
    lane: "control",
    column: "Development",
    type: "Queue",
    tone: "hcp",
    actor: "Director",
    risk: "high",
    links: ["g003", "f003", "m001"],
    description:
      "Open HCP. WP-003 implementation entry gate. Director 자율 시점 대기 — TossPayments test merchant signup + sandbox key + .env.local 도입. " +
      "본 gate 후에만 T-5~T-22 진입 가능. Implementer pre-T-4 task = 0.",
  },
  {
    id: "h002",
    code: "HCP-DB-SCHEMA-EXEC-WP003",
    title: "WP-003 T-23 subscriptions ALTER · UPCOMING",
    lane: "control",
    column: "Backlog",
    type: "Queue",
    tone: "hcp",
    actor: "Director",
    risk: "med",
    links: ["g004", "f003"],
    description:
      "Upcoming HCP. WP-002 T-17 패턴 그대로 — Director self-execute via Supabase dashboard SQL editor. " +
      "Trigger: T-15 offline migration SQL 작성 + T-22 security grep PASS 후.",
  },
  {
    id: "h003",
    code: "HCP-WEBHOOK",
    title: "Real webhook URL 도입 · FUTURE",
    lane: "control",
    column: "Backlog",
    type: "Queue",
    tone: "hcp",
    actor: "Director",
    risk: "low",
    links: ["t-webhook", "f003"],
    description:
      "Future HCP. D-015 default = Option C (stub only). " +
      "Real webhook URL 등록 시 별도 Director decision (외부 SaaS 설정 + URL 노출 + spoofing 표면).",
  },

  // ---- Records (Walkthrough / Verification) ----
  {
    id: "r001",
    code: "R-001",
    title: "F-001 Walkthrough",
    lane: "control",
    column: "Done",
    type: "Record",
    tone: "done",
    actor: "Claude Code",
    links: ["f001"],
    description: "Phase 1 walkthrough record. AC-A + AC-B evidence consolidated.",
  },
  {
    id: "r002",
    code: "R-002",
    title: "F-002 Verification (ai-npi/F-002_NPI_Verification.md)",
    lane: "control",
    column: "Done",
    type: "Record",
    tone: "done",
    actor: "Claude Code",
    links: ["f002"],
    description:
      "Phase 2 Final CP evidence. AC-A 11/11 + AC-B 10/10 PASS · 52/52 tests · 5/5 routes · env grep PASS · post-migration validate PASS.",
  },
];

export const TREE: TreeNode = {
  id: "project",
  label: "Project — 12.subscription-payment-saas-platform",
  kind: "root",
  children: [
    {
      id: "product",
      label: "Product",
      kind: "group",
      children: [
        {
          id: "phase1",
          label: "Phase 1 · CLOSED",
          kind: "lane",
          laneId: "phase1",
          children: [{ id: "tree-f001", label: "F-001 / WP-001 Auth", kind: "ticket", ticketId: "f001" }],
        },
        {
          id: "phase2",
          label: "Phase 2 · CLOSED",
          kind: "lane",
          laneId: "phase2",
          children: [
            { id: "tree-f002", label: "F-002 / WP-002 Subscription", kind: "ticket", ticketId: "f002" },
            { id: "tree-g001", label: "G-001 Phase 2 Entry",         kind: "ticket", ticketId: "g001" },
            { id: "tree-g002", label: "G-002 DB Schema",             kind: "ticket", ticketId: "g002" },
          ],
        },
        {
          id: "phase3",
          label: "Phase 3 · PLANNING",
          kind: "lane",
          laneId: "phase3",
          children: [
            { id: "tree-f003", label: "F-003 / WP-003 TossPayments", kind: "ticket", ticketId: "f003" },
            { id: "tree-g003", label: "T-4 HCP Sandbox Key",         kind: "ticket", ticketId: "g003" },
            { id: "tree-g004", label: "T-23 HCP DB Schema",          kind: "ticket", ticketId: "g004" },
            { id: "tree-webhook", label: "Webhook Stub",             kind: "ticket", ticketId: "t-webhook" },
            { id: "tree-payments-table", label: "Payments Table (defer)", kind: "ticket", ticketId: "t-payments-table" },
          ],
        },
        {
          id: "phase4",
          label: "Phase 4 · Future",
          kind: "lane",
          laneId: "phase4",
          children: [{ id: "tree-f004", label: "F-004 / WP-004 BillingCycle", kind: "ticket", ticketId: "f004" }],
        },
        {
          id: "phase5",
          label: "Phase 5 · Future",
          kind: "lane",
          laneId: "phase5",
          children: [{ id: "tree-f005", label: "F-005 / WP-005 Invoice", kind: "ticket", ticketId: "f005" }],
        },
      ],
    },
    {
      id: "meta",
      label: "Meta Sprint 1 · ACTIVE",
      kind: "lane",
      laneId: "meta",
      children: [
        { id: "tree-m001", label: "IP-001 Agent Orchestration",      kind: "ticket", ticketId: "m001" },
        { id: "tree-m002", label: "IP-002 Supabase Key Drift",       kind: "ticket", ticketId: "m002" },
        { id: "tree-m003", label: "IP-003 Feature Visibility Gap",   kind: "ticket", ticketId: "m003" },
      ],
    },
    {
      id: "control",
      label: "Control Plane",
      kind: "lane",
      laneId: "control",
      children: [
        {
          id: "hcp-queue",
          label: "HCP Queue (1 open · 2 future)",
          kind: "queue",
          laneId: "control",
          typeFilter: "Queue",
        },
        {
          id: "decision-log",
          label: "Decision Log (D-013 · D-014 · D-015)",
          kind: "queue",
          laneId: "control",
          typeFilter: "Record",
        },
      ],
    },
  ],
};

export const SUMMARY: SummaryPayload = {
  projectName: "12.subscription-payment-saas-platform",
  chips: {
    foundation: "WP-001 ✓",
    gate: "T-4 HCP",
    next: "WP-003 impl",
    hcp: 1,
  },
  // Mirrors the live mapper's Director-first 4-chip model so the static
  // seed renders identically to the SSoT path.
  display: [
    { key: "lastShipped", label: "Last shipped", value: "WP-002 Subscription Core", sub: "D-013",                tone: "green"   },
    { key: "now",         label: "Now",          value: "Phase 3 · WP-003",         sub: "Planning accepted",    tone: "blue"    },
    { key: "awaiting",    label: "Awaiting you", value: "Toss 샌드박스 키 발급",     sub: "T-4 · Director",       tone: "amber"   },
    { key: "blockers",    label: "Blockers",     value: "0",                        sub: "no immediate block",   tone: "neutral" },
  ],
};
