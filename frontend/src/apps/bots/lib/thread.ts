// The thread's pure rules (OpenBot chat.js render()'s thread half): session dividers, the "New" rule's slot, which
// approval/question cards are live, the answer an old card keeps ticked, and the search predicate. No DOM here.
import type { Bot, BotEvent } from "./api";
import { lastSeqOf } from "./derive";

/** Silence that starts a new "session" and earns a centered timestamp (seconds). */
export const SESSION_GAP_S = 15 * 60;

/** System notes surface as toasts, never in the thread (an empty .ev wrapper keeps the row count aligned). */
export const isNoise = (e: BotEvent): boolean => e.role === "system";

/** A `.day` divider goes above `e`: the first event, or one after more than SESSION_GAP_S of silence. `prev` is the raw previous event (system notes included). */
export const sessionBreak = (prev: BotEvent | undefined, e: BotEvent): boolean => !prev || e.ts - prev.ts > SESSION_GAP_S;

/** Index of the first shown message past the "New" mark; -1 when there is no mark or nothing past it. */
export const firstNewIndex = (evs: BotEvent[], mark: number | null | undefined): number =>
  mark == null ? -1 : evs.findIndex((e) => e.seq > mark && !isNoise(e));

/** The first message you sent after the card at `seq` (your answer to it), if any. */
export const answerTo = (evs: BotEvent[], seq: number): BotEvent | undefined => evs.find((x) => x.seq > seq && x.role === "user");

/**
 * The approval / question cards that are interactive right now. Only the one the bot waits on is live; an unanswered
 * app offer stays clickable once the bot has moved on (idle with `pending_offer` naming it): a click sends its label,
 * which send() settles without a model call.
 */
export function liveCards(evs: BotEvent[], b: Pick<Bot, "status" | "pending_offer">): Set<number> {
  const lastAsk = Math.max(lastSeqOf(evs, "approval"), lastSeqOf(evs, "question"));
  const out = new Set<number>();
  for (const e of evs) {
    if (e.role !== "approval" && e.role !== "question") continue;
    const answered = !!answerTo(evs, e.seq);
    if ((b.status === "waiting" && e.seq === lastAsk && !answered) || (b.status === "idle" && !answered && b.pending_offer?.seq === e.seq)) out.add(e.seq);
  }
  return out;
}

/** The answer an answered question keeps ticked: your reply, trimmed and lower-cased (compare with optionKey). Null when unanswered. */
export function chosenOption(evs: BotEvent[], seq: number): string | null {
  const a = answerTo(evs, seq);
  return a ? optionKey(a.text) : null;
}
/** How an option label and an answer are compared. */
export const optionKey = (s: string | null | undefined): string => String(s ?? "").trim().toLowerCase();

// ---- search: filters the rendered thread in place ----
/** The query as applySearch compares it. */
export const searchQuery = (raw: string): string => raw.trim().toLowerCase();
/** A row (its textContent) matches the normalized query. */
export const searchHit = (text: string | null | undefined, q: string): boolean => String(text ?? "").toLowerCase().includes(q);
/** "3 matches", "1 match", "No matches". */
export const searchCountText = (n: number): string => (n ? `${n} match${n === 1 ? "" : "es"}` : "No matches");
