// The new-bot chooser's data (OpenBot dialogs.js pickPreset): the four blank starters, each card's searchable text,
// the search filter, Enter's pick, and what the bot dialog opens with for a pick. dialogs/PresetPicker.tsx draws it.
import type { Face, Preset } from "./api";

/** A blank starter: a named face, no playbooks. */
export interface Blank { name: string; face: Face }
// Four blank starters fill the first row: a named face each, no playbooks.
export const BLANKS: Blank[] = [
  { name: "Orange Bot", face: { shape: "cloud", color: "#f0762a" } },
  { name: "Pink Bot", face: { shape: "triangle", color: "#d33f8e" } },
  { name: "Blue Bot", face: { shape: "square", color: "#2f7ae5" } },
  { name: "Red Bot", face: { shape: "circle", color: "#d33b3b" } },
];

/** What the chooser resolves with: a preset, or a blank starter. */
export type NewBotPick = { kind: "preset"; preset: Preset } | { kind: "blank"; blank: Blank };

// Each card carries its searchable text (name, key, playbook titles); blank cards match the search on their name only.
export const presetQ = (p: Pick<Preset, "name" | "key" | "skills">): string => [p.name, p.key, ...(p.skills || [])].join(" ").toLowerCase();
export const blankQ = (b: Pick<Blank, "name">): string => `${b.name.toLowerCase()} blank`;

/** The search box's words (lower-cased, blanks dropped). */
export const queryWords = (query: string): string[] => query.toLowerCase().split(/\s+/).filter(Boolean);
/** A card shows when every word of the query is in its text. */
export const matchQ = (q: string, words: string[]): boolean => words.every((w) => q.includes(w));

/** One chooser card: `key` is the preset key, "" for a blank. */
export interface PickCard { key: string; q: string; pick: NewBotPick }
export const pickCards = (presets: Preset[]): PickCard[] => [
  ...BLANKS.map((b): PickCard => ({ key: "", q: blankQ(b), pick: { kind: "blank", blank: b } })),
  ...presets.map((p): PickCard => ({ key: p.key, q: presetQ(p), pick: { kind: "preset", preset: p } })),
];

/** The cards the query leaves showing, and whether to show the "No preset matches" line. */
export function filterCards(cards: PickCard[], query: string): { shown: PickCard[]; none: boolean } {
  const words = queryWords(query), shown = cards.filter((c) => matchQ(c.q, words));
  return { shown, none: !!words.length && !shown.length };
}
/** Enter in the search box picks the first preset still showing (not a blank, unless nothing else is left). */
export const firstPick = (shown: PickCard[]): PickCard | undefined => shown.find((c) => c.key) || shown[0];

/** "Comes with N playbooks: a, b. Edit them under Skills once the bot exists." for a preset bot's dialog. */
export const presetNote = (p: Pick<Preset, "skills">): string =>
  `Comes with ${p.skills.length} playbooks: ${p.skills.join(", ")}. Edit them under Skills once the bot exists.`;

/** What the bot dialog opens with for a pick (OpenBot's `$("add").onclick`). */
export function newBotInit(pick: NewBotPick): { title: string; name: string; model: string; instructions: string; face: Face; presetNote: string; preset: string } {
  if (pick.kind === "preset") {
    const p = pick.preset;
    return { title: `New ${p.name} bot`, name: `${p.name} bot`, model: p.model || "sonnet", instructions: p.instructions || "",
      face: { icon: p.key, color: p.color }, presetNote: presetNote(p), preset: p.key };
  }
  const b = pick.blank;
  return { title: `New ${b.name}`, name: b.name, model: "sonnet", instructions: "", face: b.face, presetNote: "", preset: "" };
}
