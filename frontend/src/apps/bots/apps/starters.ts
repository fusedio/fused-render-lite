// Starter apps (OpenBot apps.js starterBadge / renderStarters / loadStarters), the non-React half: the apps that ship
// with the Render App (GET /api/apps/starters). Install copies a starter into the apps root, Update refreshes an
// installed copy (confirmed: a build may have edited it), and the badge says whether the app's one-time setup is done
// (its *_status tool, read by GET /api/apps/starters/status after the strip is drawn). StartersStrip.tsx draws it.
import type { Starter } from "../lib/api";

/** Where OpenBot's strings say "OpenBot": the product the starters ship with here. */
export const SHIPS_WITH = "FusedBot";

/** A starter as the strip holds it: `ready` is null until the status call says otherwise (or when it cannot tell). */
export type StarterRow = Starter & { ready: boolean | null };

export const starterRows = (list: Starter[] | null | undefined): StarterRow[] => (list || []).map((s) => ({ ...s, ready: null }));

/** The status call is slow: only make it when some installed starter has a setup tool. */
export const needsStatus = (rows: StarterRow[]): boolean => rows.some((s) => s.installed && !!s.setup_tool);

/** Fold the status reply in: only keys it reports change. */
export const withStatus = (rows: StarterRow[], ready: Record<string, boolean | null> | null | undefined): StarterRow[] =>
  rows.map((s) => (ready && s.key in ready ? { ...s, ready: ready[s.key] } : s));

/** The state badge: nothing until installed; Needs setup / Ready from the status tool; otherwise Installed. */
export function starterBadge(s: Pick<StarterRow, "installed" | "ready">): { text: string; cls: string; title?: string } | null {
  if (!s.installed) return null;
  if (s.ready === false) return { text: "Needs setup", cls: "badge setup", title: "Open the app once and finish its setup (the Google apps need a service-account key)" };
  if (s.ready === true) return { text: "Ready", cls: "badge ready" };
  return { text: "Installed", cls: "badge" };
}

export type StarterKind = "install" | "update" | "open";
/** The row's button: Install (not installed), Update (a newer version ships), else Open. */
export function starterButton(s: Pick<StarterRow, "installed" | "update" | "version">): { kind: StarterKind; label: string; primary: boolean; title?: string } {
  if (!s.installed) return { kind: "install", label: "Install", primary: true };
  if (s.update) return { kind: "update", label: "Update", primary: false, title: `Replace the installed files with the version that ships with ${SHIPS_WITH} (v${s.version})` };
  return { kind: "open", label: "Open", primary: false };
}

/** The busy label while an install / update runs. */
export const busyLabel = (kind: StarterKind): string => (kind === "update" ? "Updating…" : "Installing…");

/** The Update confirm (askConfirm title, text). */
export const updateConfirm = (s: Pick<StarterRow, "name" | "dir" | "version">): [string, string] => [
  `Update ${s.name}?`,
  `This replaces the app's files under ${s.dir} with the ones that ship with ${SHIPS_WITH} (v${s.version}). Edits a build made to that copy are lost; its saved key and library under .fused/ stay.`,
];
