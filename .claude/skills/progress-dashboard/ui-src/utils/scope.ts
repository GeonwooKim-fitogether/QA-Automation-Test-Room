// Tree node → board scope resolver.

import type { LaneId, Scope, Ticket, TicketType, TreeNode } from "../types";

const ALL_LANES: LaneId[] = [
  "phase1",
  "phase2",
  "phase3",
  "phase4",
  "phase5",
  "meta",
  "control",
];

export function resolveScope(node: TreeNode | null): Scope {
  if (!node) return { lanes: ALL_LANES };
  switch (node.kind) {
    case "root":
      return { lanes: ALL_LANES };
    case "group":
      if (node.id === "product")
        return { lanes: ["phase1", "phase2", "phase3", "phase4", "phase5"] };
      return { lanes: ALL_LANES };
    case "lane":
      return node.laneId ? { lanes: [node.laneId] } : { lanes: ALL_LANES };
    case "queue":
      return {
        lanes: node.laneId ? [node.laneId] : ALL_LANES,
        typeFilter: node.typeFilter,
      };
    case "ticket":
      // Ticket selection focuses the card; scope stays at parent lane for context.
      return { lanes: ALL_LANES, focusedTicketId: node.ticketId };
    default:
      return { lanes: ALL_LANES };
  }
}

export function applyScope(tickets: Ticket[], scope: Scope): Ticket[] {
  return tickets.filter((t) => {
    if (!scope.lanes.includes(t.lane)) return false;
    if (scope.typeFilter && t.type !== scope.typeFilter) return false;
    return true;
  });
}

export function findTreeNodeById(root: TreeNode, id: string): TreeNode | null {
  if (root.id === id) return root;
  for (const child of root.children ?? []) {
    const hit = findTreeNodeById(child, id);
    if (hit) return hit;
  }
  return null;
}

export function findTreeNodeByTicketId(
  root: TreeNode,
  ticketId: string
): TreeNode | null {
  if (root.kind === "ticket" && root.ticketId === ticketId) return root;
  for (const child of root.children ?? []) {
    const hit = findTreeNodeByTicketId(child, ticketId);
    if (hit) return hit;
  }
  return null;
}

export function flattenTypeFilterFromTree(node: TreeNode | null): TicketType | undefined {
  return node?.typeFilter;
}
