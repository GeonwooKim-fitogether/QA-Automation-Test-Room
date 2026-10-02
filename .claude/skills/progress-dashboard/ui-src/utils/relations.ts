// Relationship link resolution between tickets.

import type { Ticket } from "../types";

/**
 * Build a bidirectional link set for a focused ticket id:
 * - outgoing: ticket.links
 * - incoming: any ticket whose links contain the focused id
 */
export function relatedIdSet(focusedId: string | null, tickets: Ticket[]): Set<string> {
  const set = new Set<string>();
  if (!focusedId) return set;
  set.add(focusedId);
  const focused = tickets.find((t) => t.id === focusedId);
  for (const out of focused?.links ?? []) set.add(out);
  for (const t of tickets) {
    if (t.links?.includes(focusedId)) set.add(t.id);
  }
  return set;
}

export function isLinked(focusedId: string | null, ticketId: string, related: Set<string>): boolean {
  if (!focusedId) return true; // no focus → all tickets are "live"
  return related.has(ticketId);
}

export function lookupTicket(tickets: Ticket[], id: string | null): Ticket | null {
  if (!id) return null;
  return tickets.find((t) => t.id === id) ?? null;
}
