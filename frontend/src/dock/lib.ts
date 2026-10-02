// The menu-bar Dock tray's pure parts (dock.ts is the DOM): the GET /api/dock payload's types, the tray order built
// from it, the node-map key, and a bot's face as an SVG string (the same drawing as apps/bots/components/Face.tsx,
// at rest: no mood pose, no animation layers). src/dock is a src-root entry, so it may import apps/bots/lib
// (scripts/check-boundaries.mjs); face.ts is pure data, and its only import is a type.
import { BRANDS, FACE_SHAPES, faceOf } from "../apps/bots/lib/face";

export interface DockFace { shape?: string; color?: string; icon?: string }
export interface BotRow {
  kind: "bot"; id: string; name: string; face?: DockFace | null; status?: string; running?: boolean; pinned?: boolean;
}
export interface AppRow { kind: "app"; dir: string; name: string; icon?: boolean; pinned?: boolean; mtime?: number }
export type Row = BotRow | AppRow;
/** GET /api/dock. `pinned` holds bots first, then apps (the server's order); the recents exclude the pinned. */
export interface DockPayload { pinned?: Row[]; recent_bots?: BotRow[]; recent_apps?: AppRow[]; tilesize?: number }

/** How many recent bots, and then how many recent apps, the tray shows right of the separator. */
export const RECENT_CAP = 3;

/** The node-map key: one button per bot or app folder, kept across polls. */
export const tileKey = (r: Row): string => (r.kind === "bot" ? "bot:" + r.id : "app:" + r.dir);

const isBot = (r: unknown): r is BotRow =>
  !!r && (r as Row).kind === "bot" && typeof (r as BotRow).id === "string" && !!(r as BotRow).id;
const isApp = (r: unknown): r is AppRow =>
  !!r && (r as Row).kind === "app" && typeof (r as AppRow).dir === "string" && !!(r as AppRow).dir;

/**
 * Left of the separator: pinned bots, then pinned apps (each in the server's order). Right of it: up to RECENT_CAP
 * recent bots, then up to RECENT_CAP recent apps. A row pinned in the payload never shows twice (a recent that is
 * also pinned is dropped before the cap counts it), and every row's `pinned` is set by the zone it lands in.
 */
export function trayOrder(d: DockPayload | null | undefined): { pinned: Row[]; recent: Row[] } {
  const seen = new Set<string>();
  const take = <T extends Row>(rows: T[], pinned: boolean, cap = Infinity): T[] => {
    const out: T[] = [];
    for (const r of rows) {
      if (out.length >= cap) break;
      const k = tileKey(r);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ ...r, pinned });
    }
    return out;
  };
  const pins = Array.isArray(d?.pinned) ? d!.pinned : [];
  const pinned: Row[] = [...take(pins.filter(isBot), true), ...take(pins.filter(isApp), true)];
  const bots = Array.isArray(d?.recent_bots) ? d!.recent_bots.filter(isBot) : [];
  const apps = Array.isArray(d?.recent_apps) ? d!.recent_apps.filter(isApp) : [];
  const recent: Row[] = [...take(bots, false, RECENT_CAP), ...take(apps, false, RECENT_CAP)];
  return { pinned, recent };
}

/** A tile's plain name (the native menu's title, the aria-label). */
export const displayName = (r: Row): string =>
  (r.name || "").trim() || (r.kind === "bot" ? r.id : r.dir.replace(/\/+$/, "").split("/").pop() || r.dir);

/** The bubble under a hovered tile: the name, and a bot's status when it is not idle. */
export const bubbleText = (r: Row): string =>
  displayName(r) + (r.kind === "bot" && r.status && r.status !== "idle" ? " · " + r.status : "");

/** POST /api/dock/open's body. */
export const openBody = (r: Row): { kind: "bot"; id: string } | { kind: "app"; dir: string } =>
  r.kind === "bot" ? { kind: "bot", id: r.id } : { kind: "app", dir: r.dir };

/** The HTML menu's "Open in Browser": an app's index page in the renderer, a bot's chat on the bots page. */
export const browserHref = (r: Row, origin: string): string =>
  r.kind === "bot"
    ? origin + "/?bot=" + encodeURIComponent(r.id)
    : origin + "/render?path=" + encodeURIComponent(r.dir.replace(/\/+$/, "") + "/index.html");

/** Same URL as apps/bots/lib/api.ts appIconUrl (not imported: that module carries the bots page's request state). */
export const appIconUrl = (dir: string): string => "/api/apps/icon?dir=" + encodeURIComponent(dir);

/**
 * A bot's face, drawn as Face.tsx draws it: viewBox -1.25 -1.25 102.5 102.5; a brand face is the disc with the
 * mark at translate(26 26) scale(2); a blob is the shape, the highlight ellipse and two eyes. faceOf validates the
 * row's face (a bad shape/colour/icon falls back exactly as on the bots page), so every interpolated value is a
 * known path, a palette colour or a #rrggbb hex: nothing from the wire reaches the markup unchecked.
 */
export function faceSvg(b: { id?: string; name?: string; face?: DockFace | null }): string {
  const { shape, color, icon } = faceOf({ id: b.id, name: b.name, face: b.face || undefined });
  const body = icon
    ? `<circle cx="50" cy="50" r="38" fill="${color}"/>`
      + `<g transform="translate(26 26) scale(2)" fill="#fff">${BRANDS[icon].glyph(color)}</g>`
    : `<path d="${FACE_SHAPES[shape]}" fill="${color}"/>`
      + `<ellipse cx="37" cy="31" rx="9" ry="4.5" fill="#fff" opacity=".28" transform="rotate(-28 37 31)"/>`
      + [41, 54].map((x) => `<rect x="${x}" y="43" width="5" height="12" rx="2.5" fill="#0f172a"/>`).join("");
  return `<svg viewBox="-1.25 -1.25 102.5 102.5" aria-hidden="true">${body}</svg>`;
}

// ---------- the app fallback tile (the old dock.html's, verbatim) ----------
export function hash(s: string): number {
  let h = 2166136261;
  for (const c of s) { h ^= c.codePointAt(0)!; h = Math.imul(h, 16777619); }
  return h >>> 0;
}
// 16 muted, mid-dark tones that hold white type and sit quietly next to real icons.
export const PALETTE = [
  "#5b6b8c", "#6b5b8c", "#8c5b7a", "#8c5f5b", "#8c7a5b", "#6f8c5b", "#5b8c7a", "#5b7f8c",
  "#4f5d75", "#705a7c", "#7c5a66", "#7c6a5a", "#6a7c5a", "#5a7c70", "#5a6e7c", "#666b7c",
];
/** Solid palette colour (hashed from the name) and the name's first character, upper-cased. */
export function fallbackTile(name: string): { color: string; letter: string } {
  const clean = (name || "").trim();
  const first = clean ? String.fromCodePoint(clean.codePointAt(0)!) : "?";
  return { color: PALETTE[hash(clean || "?") % PALETTE.length], letter: first.toUpperCase() };
}
