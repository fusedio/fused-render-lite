import { describe, expect, test } from "bun:test";
import { BRANDS, FACE_COLORS, FACE_SHAPES } from "../apps/bots/lib/face";
import {
  PALETTE, RECENT_CAP, browserHref, bubbleText, displayName, fallbackTile, faceSvg, openBody, tileKey, trayOrder,
  type AppRow, type BotRow,
} from "./lib";

const bot = (id: string, extra: Partial<BotRow> = {}): BotRow => ({ kind: "bot", id, name: id.toUpperCase(), ...extra });
const app = (dir: string, extra: Partial<AppRow> = {}): AppRow => ({ kind: "app", dir, name: dir.split("/").pop()!, ...extra });
const keys = (rows: { kind: string }[]) => rows.map((r) => tileKey(r as BotRow | AppRow));

describe("tileKey", () => {
  test("bots by id, apps by folder", () => {
    expect(tileKey(bot("b1"))).toBe("bot:b1");
    expect(tileKey(app("/x/apps/notes"))).toBe("app:/x/apps/notes");
  });
  test("a bot id and an app folder with the same text never collide", () => {
    expect(tileKey(bot("notes"))).not.toBe(tileKey(app("notes")));
  });
});

describe("trayOrder", () => {
  test("pinned bots lead, then pinned apps, whatever order the payload mixes them in", () => {
    const t = trayOrder({ pinned: [app("/a/one", { pinned: true }), bot("b2", { pinned: true }), app("/a/two", { pinned: true }), bot("b1", { pinned: true })] });
    expect(keys(t.pinned)).toEqual(["bot:b2", "bot:b1", "app:/a/one", "app:/a/two"]);
    expect(t.pinned.every((r) => r.pinned)).toBe(true);
    expect(t.recent).toEqual([]);
  });
  test("recents: up to 3 bots, then up to 3 apps", () => {
    const t = trayOrder({
      recent_bots: ["r1", "r2", "r3", "r4", "r5"].map((id) => bot(id)),
      recent_apps: ["/a/1", "/a/2", "/a/3", "/a/4"].map((d) => app(d)),
    });
    expect(RECENT_CAP).toBe(3);
    expect(keys(t.recent)).toEqual(["bot:r1", "bot:r2", "bot:r3", "app:/a/1", "app:/a/2", "app:/a/3"]);
    expect(t.recent.every((r) => !r.pinned)).toBe(true);
  });
  test("a recent that is also pinned shows once, left of the separator, and does not use up a recent slot", () => {
    const t = trayOrder({
      pinned: [bot("b1", { pinned: true }), app("/a/1", { pinned: true })],
      recent_bots: [bot("b1"), bot("r1"), bot("r2"), bot("r3")],
      recent_apps: [app("/a/1", { pinned: true }), app("/a/2")],
    });
    expect(keys(t.pinned)).toEqual(["bot:b1", "app:/a/1"]);
    expect(keys(t.recent)).toEqual(["bot:r1", "bot:r2", "bot:r3", "app:/a/2"]);
  });
  test("the zone decides `pinned`, not the row's own flag", () => {
    const t = trayOrder({ pinned: [bot("b1", { pinned: false })], recent_apps: [app("/a/1", { pinned: true })] });
    expect(t.pinned[0].pinned).toBe(true);
    expect(t.recent[0].pinned).toBe(false);
  });
  test("an empty, partial or malformed payload is an empty tray, and bad rows are skipped", () => {
    expect(trayOrder(undefined)).toEqual({ pinned: [], recent: [] });
    expect(trayOrder({})).toEqual({ pinned: [], recent: [] });
    const t = trayOrder({
      pinned: [null, { kind: "bot" }, { kind: "app", dir: "" }, { kind: "other", id: "x" }, bot("ok")] as never,
      recent_bots: "nope" as never,
      recent_apps: [app("/a/1"), { kind: "bot", id: "wrong-list" }] as never,
    });
    expect(keys(t.pinned)).toEqual(["bot:ok"]);
    expect(keys(t.recent)).toEqual(["app:/a/1"]);
  });
  test("does not mutate the payload's rows", () => {
    const r = app("/a/1", { pinned: true });
    trayOrder({ recent_apps: [r] });
    expect(r.pinned).toBe(true);
  });
});

describe("faceSvg", () => {
  test("a blob: the shape path, the highlight, two eyes, in Face.tsx's viewBox", () => {
    const s = faceSvg({ id: "b1", face: { shape: "cloud", color: FACE_COLORS[4] } });
    expect(s.startsWith('<svg viewBox="-1.25 -1.25 102.5 102.5"')).toBe(true);
    expect(s).toContain(`<path d="${FACE_SHAPES.cloud}" fill="${FACE_COLORS[4]}"/>`);
    expect(s).toContain('<ellipse cx="37" cy="31" rx="9" ry="4.5" fill="#fff" opacity=".28" transform="rotate(-28 37 31)"/>');
    expect(s).toContain('<rect x="41" y="43" width="5" height="12" rx="2.5"');
    expect(s).toContain('<rect x="54" y="43" width="5" height="12" rx="2.5"');
    expect(s).not.toContain("<circle");
  });
  test("a brand: the disc in the face colour and the mark at translate(26 26) scale(2), no eyes", () => {
    const s = faceSvg({ id: "b1", face: { icon: "youtube", color: "#123456" } });
    expect(s).toContain('<circle cx="50" cy="50" r="38" fill="#123456"/>');
    expect(s).toContain(`<g transform="translate(26 26) scale(2)" fill="#fff">${BRANDS.youtube.glyph("#123456")}</g>`);
    expect(s).not.toContain("<rect x=\"41\"");
    expect(s).not.toContain("<ellipse cx=\"37\"");
  });
  test("bad face data falls back as faceOf does: nothing unvalidated reaches the markup", () => {
    const s = faceSvg({ id: "b1", face: { shape: '"><script>', color: '"/><script>', icon: "nope" } });
    expect(s).not.toContain("<script>");
    expect(s).toMatch(/<path d="[^"]+" fill="#[0-9a-f]{6}"\/>/i);
    // no face at all: the id-hashed blob, the same one the bots page draws
    expect(faceSvg({ id: "b1" })).toBe(faceSvg({ id: "b1", face: null }));
  });
});

describe("labels and links", () => {
  test("the bubble adds a bot's status unless idle", () => {
    expect(bubbleText(bot("b1", { name: "Scout", status: "idle" }))).toBe("Scout");
    expect(bubbleText(bot("b1", { name: "Scout", status: "running" }))).toBe("Scout · running");
    expect(bubbleText(bot("b1", { name: "Scout" }))).toBe("Scout");
    expect(bubbleText(app("/a/notes", { name: "Notes" }))).toBe("Notes");
  });
  test("a nameless row is named by its id or folder", () => {
    expect(displayName(bot("b9", { name: "" }))).toBe("b9");
    expect(displayName(app("/x/apps/notes/", { name: "  " }))).toBe("notes");
  });
  test("open bodies", () => {
    expect(openBody(bot("b1", { name: "x", pinned: true }))).toEqual({ kind: "bot", id: "b1" });
    expect(openBody(app("/a/n"))).toEqual({ kind: "app", dir: "/a/n" });
  });
  test("Open in Browser: an app's index page in the renderer, a bot's chat", () => {
    expect(browserHref(app("/x/my apps/n"), "http://h:1")).toBe("http://h:1/render?path=" + encodeURIComponent("/x/my apps/n/index.html"));
    expect(browserHref(bot("b 1"), "http://h:1")).toBe("http://h:1/?bot=b%201");
  });
  test("fallback tile: stable palette colour, first character upper-cased", () => {
    const f = fallbackTile("notes");
    expect(f.letter).toBe("N");
    expect(PALETTE).toContain(f.color);
    expect(fallbackTile("notes")).toEqual(f);
    expect(fallbackTile("").letter).toBe("?");
  });
});
