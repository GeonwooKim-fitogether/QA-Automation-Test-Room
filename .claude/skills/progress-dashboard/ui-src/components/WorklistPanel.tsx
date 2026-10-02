// WorklistPanel — T-NNN drill-down rendered inside the DetailDrawer.
//
// v3 PR-7: 4-state visualization (mock δ).
//   - done : 채워진 초록 ●
//   - prog : 반쪽 청록 ◐ (in-progress)
//   - wait : 채워진 회색 ● (not-started, regular pending — 그냥 대기)
//   - risk : 채워진 빨강 ● (blocked / HCP / Director 답변 필요)
// 모두 동일한 원, 색만 다름. prog 만 half-fill 로 시각 구분 보존.
//
// 5-state (parser) → 4-state (presentation) mapping:
//   done         → done
//   in-progress  → prog
//   blocked      → risk   (진짜 막힘 — out-of-cycle / rejected / blocked keyword)
//   pending      → wait   (기본 대기 — director / review 키워드 포함, 대부분)
//   not-started  → wait   (⚪ / 미분류)
// risk 는 명시적 blocked 만. 모든 pending 까지 risk 로 칠하면
// 카드 전체가 빨강으로 묻혀 4-state 구분이 사라짐 (실제 데이터 검증).
//
// Phase 헤더에는 stage badge — phase 안 task 들의 최악 상태 (risk > prog > wait > done).

import { TOKENS } from "../styles/atlassianTokens";
import { vscodeFileUrl } from "../utils/docs";
import type { WorklistData, WorklistTask, WorklistTaskStatus } from "../types";

interface Props {
  worklist: WorklistData;
  /**
   * The same card's progress figure as shown on the board card, which is
   * derived from a different source (git commits or the state sentinel). When
   * the two disagree the panel says so in one line instead of leaving two bare
   * numbers on screen for the reader to arbitrate between.
   */
  cardProgress?: { done: number; total: number; source: "sentinel" | "commits" | "merged" };
}

type TaskState = "done" | "prog" | "wait" | "risk";

function toTaskState(s: WorklistTaskStatus): TaskState {
  switch (s) {
    case "done":         return "done";
    case "in-progress":  return "prog";
    case "blocked":      return "risk";
    case "pending":      return "wait";
    case "not-started":  return "wait";
  }
}

const STATE_COLOR: Record<TaskState, string> = {
  done: TOKENS.green,
  prog: TOKENS.active,
  wait: TOKENS.textMuted,
  risk: TOKENS.red,
};

const STATE_LABEL: Record<TaskState, string> = {
  done: "done",
  prog: "in progress",
  wait: "waiting",
  risk: "needs review",
};

// stage state = phase 안 task 들의 최악 상태 (risk > prog > wait > done).
function phaseState(tasks: WorklistTask[]): TaskState {
  let saw = { risk: false, prog: false, wait: false };
  for (const t of tasks) {
    const s = toTaskState(t.status);
    if (s === "risk") saw.risk = true;
    else if (s === "prog") saw.prog = true;
    else if (s === "wait") saw.wait = true;
  }
  if (saw.risk) return "risk";
  if (saw.prog) return "prog";
  if (saw.wait) return "wait";
  return "done";
}

function groupByPhase(tasks: WorklistTask[]): { phase: string; tasks: WorklistTask[] }[] {
  const order: string[] = [];
  const groups = new Map<string, WorklistTask[]>();
  for (const t of tasks) {
    const key = t.phase || "Other";
    if (!groups.has(key)) {
      order.push(key);
      groups.set(key, []);
    }
    groups.get(key)!.push(t);
  }
  return order.map((phase) => ({ phase, tasks: groups.get(phase)! }));
}

export function WorklistPanel({ worklist, cardProgress }: Props) {
  const { totals, tasks, code, relPath, absPath } = worklist;
  // Only worth explaining when the two figures actually differ. Identical
  // numbers need no disclaimer, and adding one anyway would be noise.
  const disagrees =
    !!cardProgress &&
    (cardProgress.done !== totals.done || cardProgress.total !== totals.total);
  const groups = groupByPhase(tasks);
  const pctDone = totals.total > 0
    ? Math.round((totals.done / totals.total) * 100)
    : 0;

  // 4-state aggregate counts (UI mapping)
  const stateCounts = { done: 0, prog: 0, wait: 0, risk: 0 } as Record<TaskState, number>;
  for (const t of tasks) stateCounts[toTaskState(t.status)] += 1;

  return (
    <section
      aria-label={`${code} task breakdown`}
      style={{
        background: TOKENS.bgWhite,
        border: `1px solid ${TOKENS.border}`,
        borderRadius: 4,
        padding: 10,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", minWidth: 0, gap: 2 }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: 0.4,
              textTransform: "uppercase",
              color: TOKENS.textMuted,
            }}
          >
            {code} Worklist
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, color: TOKENS.textPrimary, fontWeight: 600 }}>
              {totals.done}/{totals.total} done
            </span>
            {/* Names where this number came from, mirroring the `commits` /
                `sentinel` tag printed on the board card. */}
            <span
              title="이 문서의 태스크 표에서 완료 표시된 줄을 센 값입니다."
              style={{ fontSize: 10, color: TOKENS.textMuted }}
            >
              worklist
            </span>
            {(["prog", "wait", "risk"] as TaskState[]).map((s) =>
              stateCounts[s] > 0 ? (
                <StateCountChip key={s} state={s} count={stateCounts[s]} />
              ) : null,
            )}
          </div>
        </div>
        <a
          href={vscodeFileUrl(absPath)}
          title={`Open ${relPath} in VSCode`}
          style={{
            flexShrink: 0,
            fontSize: 10,
            color: TOKENS.blueDark,
            textDecoration: "none",
            border: `1px solid ${TOKENS.border}`,
            borderRadius: 3,
            padding: "2px 6px",
            background: TOKENS.bgWhite,
          }}
        >
          edit ↗
        </a>
      </header>

      {/* Progress bar */}
      <div
        title={`${pctDone}% complete`}
        style={{
          height: 5,
          borderRadius: 3,
          background: TOKENS.divider,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pctDone}%`,
            background: TOKENS.green,
            transition: "width 0.3s ease",
          }}
        />
      </div>

      {disagrees && cardProgress && (
        <div
          className="rounded px-2 py-1.5 text-[11px] leading-relaxed"
          style={{
            background: TOKENS.bg,
            border: `1px solid ${TOKENS.divider}`,
            color: TOKENS.textSecondary,
          }}
        >
          카드에 적힌 <strong>T-{cardProgress.done}/{cardProgress.total}</strong> 와 위의{" "}
          <strong>{totals.done}/{totals.total}</strong> 는 세는 대상이 달라서 다릅니다. 카드
          숫자는 {cardProgress.source === "sentinel" ? "state 파일의 상태 문구" : "git 커밋 기록"}
          에서 유도했고, 위 숫자는 이 문서의 완료 표시를 셌습니다. 둘 다 맞는 값입니다.
        </div>
      )}

      {groups.map(({ phase, tasks: groupTasks }) => {
        const sState = phaseState(groupTasks);
        return (
          <div key={phase} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                marginTop: 4,
              }}
            >
              <StageBadge state={sState} />
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: TOKENS.textMuted,
                  textTransform: "uppercase",
                  letterSpacing: 0.4,
                }}
              >
                {phase}
              </span>
            </div>
            {groupTasks.map((t) => (
              <TaskRow key={t.id} task={t} />
            ))}
          </div>
        );
      })}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Atoms — task dot / stage badge / count chip
// ---------------------------------------------------------------------------

function TaskDot({ state }: { state: TaskState }) {
  const color = STATE_COLOR[state];
  if (state === "prog") {
    // half-filled circle — left half empty (with stroke), right half filled.
    return (
      <svg
        width={10}
        height={10}
        viewBox="0 0 10 10"
        aria-hidden
        style={{ flexShrink: 0, marginTop: 3 }}
      >
        <circle cx={5} cy={5} r={4} fill="none" stroke={color} strokeWidth={1.2} />
        <path d="M 5 1 A 4 4 0 0 1 5 9 Z" fill={color} />
      </svg>
    );
  }
  return (
    <span
      aria-hidden
      style={{
        flexShrink: 0,
        display: "inline-block",
        width: 8,
        height: 8,
        marginTop: 4,
        borderRadius: "50%",
        background: color,
        boxShadow: state === "wait" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
      }}
    />
  );
}

function StageBadge({ state }: { state: TaskState }) {
  // Phase 헤더용 — TaskDot 보다 한 단계 큼.
  const color = STATE_COLOR[state];
  if (state === "prog") {
    return (
      <svg width={11} height={11} viewBox="0 0 10 10" aria-hidden style={{ flexShrink: 0 }}>
        <circle cx={5} cy={5} r={4} fill="none" stroke={color} strokeWidth={1.4} />
        <path d="M 5 1 A 4 4 0 0 1 5 9 Z" fill={color} />
      </svg>
    );
  }
  return (
    <span
      aria-hidden
      style={{
        flexShrink: 0,
        display: "inline-block",
        width: 9,
        height: 9,
        borderRadius: "50%",
        background: color,
        boxShadow: state === "wait" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
      }}
    />
  );
}

function StateCountChip({ state, count }: { state: TaskState; count: number }) {
  const color = STATE_COLOR[state];
  return (
    <span
      title={`${count} ${STATE_LABEL[state]}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 11,
        color: TOKENS.textSecondary,
        fontWeight: 500,
      }}
    >
      {state === "prog" ? (
        <svg width={9} height={9} viewBox="0 0 10 10" aria-hidden>
          <circle cx={5} cy={5} r={4} fill="none" stroke={color} strokeWidth={1.4} />
          <path d="M 5 1 A 4 4 0 0 1 5 9 Z" fill={color} />
        </svg>
      ) : (
        <span
          aria-hidden
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: color,
            boxShadow: state === "wait" ? `inset 0 0 0 1px ${TOKENS.border}` : "none",
          }}
        />
      )}
      {count}
    </span>
  );
}

function TaskRow({ task }: { task: WorklistTask }) {
  const state = toTaskState(task.status);
  const isDone = state === "done";
  return (
    <div
      title={`${task.id} · ${STATE_LABEL[state]}${
        task.deps ? ` · deps: ${task.deps}` : ""
      }\n${task.statusRaw}`}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        padding: "2px 0",
        fontSize: 11,
        lineHeight: 1.4,
        color: isDone ? TOKENS.textMuted : TOKENS.textPrimary,
      }}
    >
      <TaskDot state={state} />
      <span
        style={{
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontWeight: 600,
          minWidth: 36,
          flexShrink: 0,
          color: isDone ? TOKENS.textMuted : TOKENS.textSecondary,
        }}
      >
        {task.id}
      </span>
      <span
        style={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          textDecoration: isDone ? "line-through" : "none",
        }}
      >
        {task.description}
      </span>
    </div>
  );
}
