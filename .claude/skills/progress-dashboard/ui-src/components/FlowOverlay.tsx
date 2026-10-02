"use client";

// v3 PR-5: Flow 점선 SVG overlay (Obsidian backlink 스타일).
//
// 강조 카드 ↔ 인접 카드 간 bezier curve. 빈 칸 연결 X (카드↔카드만).
//   - Phase view: 같은 swimlane 안만 (parent 에서 미리 filter)
//   - Agent view: cross-swimlane 가능
//   - Bezier control offset: max(50, dx*0.45)
//
// 위치 측정 (모두 container 기준 상대 좌표 = scroll-invariant):
//   - container = 보드 innermost div (cards 의 공통 ancestor)
//   - 각 card 의 BCR 을 container 기준 좌표로 변환 → scroll 해도 차이값 불변
//   - 재측정 트리거: 초기 + 다음 frame + settle 타이머(230/480ms) +
//     container resize(ResizeObserver) + window resize.
//
// settle 재측정이 핵심: drawer 열림 0.18s grid 전환과 focus 카드 auto-scroll 이
// 동기 measure() 이후에 끝나므로, 그때 카드 위치가 바뀐 걸 다시 잡아 점선이
// 카드 모서리에서 빗나가지 않게 한다. (persistent scroll listener 는 smooth
// scroll 중 setCurves 와 re-render loop 를 만들어 쓰지 않는다.)

import { useLayoutEffect, useRef, useState } from "react";
import { TOKENS } from "../styles/atlassianTokens";

interface Props {
  containerRef: React.RefObject<HTMLDivElement>;
  focusedTicketId: string | null;
  /** 이미 swimlane filter 가 적용된 final 연결 id set */
  flowIds: Set<string>;
  enabled: boolean;
}

interface CurveSpec {
  id: string;
  d: string;
}

export function FlowOverlay({ containerRef, focusedTicketId, flowIds, enabled }: Props) {
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [curves, setCurves] = useState<CurveSpec[]>([]);

  // 측정 함수 ref — ResizeObserver / window resize callback 에서 동일 instance 호출
  const measureRef = useRef<() => void>(() => {});

  useLayoutEffect(() => {
    if (!enabled || !focusedTicketId || flowIds.size === 0) {
      setCurves([]);
      setSize({ w: 0, h: 0 });
      return;
    }
    const container = containerRef.current;
    if (!container) return;

    function measure() {
      const c = containerRef.current;
      if (!c) return;
      const cRect = c.getBoundingClientRect();
      setSize({ w: c.offsetWidth, h: c.offsetHeight });

      const focused = c.querySelector<HTMLElement>(`[data-ticket-id="${focusedTicketId}"]`);
      if (!focused) {
        setCurves([]);
        return;
      }
      const fRect = focused.getBoundingClientRect();
      const fLeft   = fRect.left   - cRect.left;
      const fRight  = fRect.right  - cRect.left;
      const fTop    = fRect.top    - cRect.top;
      const fBottom = fRect.bottom - cRect.top;
      const fMidY   = (fTop + fBottom) / 2;
      const cardH   = fBottom - fTop;

      // L/R edge 전용 routing — 효율 기반 split.
      //   - 우 column target (rLeft >= fRight)  → R edge 확정 (직진)
      //   - 좌 column target (rRight <= fLeft)  → L edge 확정 (직진)
      //   - overlap (같은 column)               → 양쪽 다 detour 라서
      //                                            R/L 중 더 적게 쌓인 쪽으로
      //                                            balancing
      // 직진이 효율적인 conn 을 detour 시키지 않음 (split 강제 X).
      type Edge = "left" | "right";
      type Conn = {
        rid: string;
        edge: Edge;
        x2: number;        // target 진입 X (좌변 or 우변)
        tOutX: 1 | -1;     // target outward 방향
        rMidY: number;
      };

      const directs: Conn[] = []; // 직진 (natural side 확정)
      const overlaps: { rid: string; rLeft: number; rRight: number; rMidY: number }[] = [];

      flowIds.forEach((rid) => {
        const r = c.querySelector<HTMLElement>(`[data-ticket-id="${rid}"]`);
        if (!r) return;
        const rRect = r.getBoundingClientRect();
        const rLeft  = rRect.left  - cRect.left;
        const rRight = rRect.right - cRect.left;
        const rMidY  = (rRect.top + rRect.bottom) / 2 - cRect.top;

        if (rLeft >= fRight) {
          // 우 column → R edge, target 좌변 진입 (정상 S)
          directs.push({ rid, edge: "right", x2: rLeft, tOutX: -1, rMidY });
        } else if (rRight <= fLeft) {
          // 좌 column → L edge, target 우변 진입 (mirrored S)
          directs.push({ rid, edge: "left", x2: rRight, tOutX: 1, rMidY });
        } else {
          overlaps.push({ rid, rLeft, rRight, rMidY });
        }
      });

      // edge 별 그룹
      const groups: Record<Edge, Conn[]> = { left: [], right: [] };
      directs.forEach((cn) => groups[cn.edge].push(cn));

      // overlap 은 양쪽 다 detour 라서 적은 쪽으로 균형. target Y 순으로 안정성 ↑.
      overlaps.sort((a, b) => a.rMidY - b.rMidY);
      overlaps.forEach((ov) => {
        const goLeft = groups.left.length <= groups.right.length;
        if (goLeft) {
          // L edge 에서 나가 target 좌변 진입 (왼쪽 호)
          groups.left.push({ rid: ov.rid, edge: "left", x2: ov.rLeft, tOutX: -1, rMidY: ov.rMidY });
        } else {
          // R edge 에서 나가 target 우변 진입 (오른쪽 호)
          groups.right.push({ rid: ov.rid, edge: "right", x2: ov.rRight, tOutX: 1, rMidY: ov.rMidY });
        }
      });

      groups.right.sort((a, b) => a.rMidY - b.rMidY);
      groups.left.sort((a, b) => a.rMidY - b.rMidY);

      const SPREAD_PER_LINE  = 8;     // 선 1개당 가산 spacing
      const MAX_SPREAD_RATIO = 0.55;  // 카드 높이의 55% 이내
      const maxSpread = cardH * MAX_SPREAD_RATIO;

      const next: CurveSpec[] = [];
      (Object.keys(groups) as Edge[]).forEach((edge) => {
        const list = groups[edge];
        const n = list.length;
        if (n === 0) return;
        const spread = Math.min(maxSpread, (n - 1) * SPREAD_PER_LINE);

        const x1 = edge === "right" ? fRight : fLeft;
        const sOutX = edge === "right" ? 1 : -1;

        list.forEach((cn, i) => {
          const exitY = n === 1 ? fMidY : fMidY - spread / 2 + (spread * i) / (n - 1);

          // 정상 S-curve / 같은-방향 호 — 직교 Y 축 blend 로 시작 tangent 부드럽게.
          const dx   = cn.x2 - x1;
          const dy   = cn.rMidY - exitY;
          const dist = Math.hypot(dx, dy);
          const cpLen = Math.max(34, Math.min(dist * 0.32, 130));
          const blend = Math.min(0.22, Math.abs(dy) / 1000);
          const c1x = x1 + sOutX * cpLen;
          const c1y = exitY + dy * blend;
          const c2x = cn.x2 + cn.tOutX * cpLen;
          const c2y = cn.rMidY - dy * blend;

          next.push({
            id: `${focusedTicketId}->${cn.rid}`,
            d: `M ${x1} ${exitY} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${cn.x2} ${cn.rMidY}`,
          });
        });
      });
      setCurves(next);
    }

    measureRef.current = measure;
    measure();

    // Bounded, one-shot re-measures (NOT a persistent scroll listener — that
    // loops with setCurves during smooth auto-scroll). Coordinates are
    // container-relative / scroll-invariant, so we only need to re-measure
    // after layout settles: next frame, after the 0.18s grid transition, and
    // after the focused-card auto-scroll (~210ms) completes.
    const raf = requestAnimationFrame(() => measureRef.current());
    const t1 = window.setTimeout(() => measureRef.current(), 230);
    const t2 = window.setTimeout(() => measureRef.current(), 480);

    const ro = new ResizeObserver(() => measureRef.current());
    ro.observe(container);
    const onResize = () => measureRef.current();
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      ro.disconnect();
      window.removeEventListener("resize", onResize);
    };
  }, [enabled, focusedTicketId, flowIds, containerRef]);

  if (!enabled || curves.length === 0) return null;

  return (
    <svg
      aria-hidden
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: size.w,
        height: size.h,
        pointerEvents: "none",
        zIndex: 1,
      }}
    >
      {/* 도착지 화살표 — focused -> target 방향 식별용 */}
      <defs>
        <marker
          id="dd-flow-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill={TOKENS.active} />
        </marker>
      </defs>
      {curves.map((c) => (
        <path
          key={c.id}
          d={c.d}
          stroke={TOKENS.active}
          strokeWidth={1.6}
          strokeDasharray="5 4"
          strokeLinecap="round"
          fill="none"
          opacity={0.85}
          markerEnd="url(#dd-flow-arrow)"
        />
      ))}
    </svg>
  );
}
