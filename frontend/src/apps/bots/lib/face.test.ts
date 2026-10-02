import { describe, expect, test } from "bun:test";
import { BRANDS, FACE_COLORS, FACE_SHAPES, faceOf, fkey } from "./face";

describe("faceOf", () => {
  test("a brand face keeps any hex colour", () => {
    expect(faceOf({ id: "b1", face: { icon: "linkedin", color: "#123456" } })).toMatchObject({ icon: "linkedin", color: "#123456" });
    expect(faceOf({ id: "b1", face: { icon: "linkedin", color: "#ABCDEF" } }).color).toBe("#ABCDEF");
  });
  test("a brand face without a usable colour takes the brand's own", () => {
    expect(faceOf({ id: "b1", face: { icon: "youtube" } }).color).toBe(BRANDS.youtube.color);
    expect(faceOf({ id: "b1", face: { icon: "youtube", color: "red" } }).color).toBe(BRANDS.youtube.color);
    expect(faceOf({ id: "b1", face: { icon: "youtube", color: "#12345" } }).color).toBe(BRANDS.youtube.color);
  });
  test("a brand face also takes a palette colour", () => {
    expect(faceOf({ id: "b1", face: { icon: "gmail", color: FACE_COLORS[3] } }).color).toBe(FACE_COLORS[3]);
  });
  test("a blob rejects a non-palette colour and falls back to the hashed palette one", () => {
    const f = faceOf({ id: "b1", face: { shape: "cloud", color: "#123456" } });
    expect(f.icon).toBe("");
    expect(f.shape).toBe("cloud");
    expect(FACE_COLORS).toContain(f.color);
    expect(f.color).not.toBe("#123456");
    expect(f.color).toBe(faceOf({ id: "b1" }).color);
  });
  test("an unknown icon is dropped (blob rules apply)", () => {
    const f = faceOf({ id: "b1", face: { icon: "nope", color: "#123456" } });
    expect(f.icon).toBe("");
    expect(FACE_COLORS).toContain(f.color);
    expect(faceOf({ id: "b1", face: { icon: "constructor" } }).icon).toBe("");
  });
  test("a brand face still resolves a shape (the picker falls back to it)", () => {
    expect(Object.keys(FACE_SHAPES)).toContain(faceOf({ id: "b1", face: { icon: "x" } }).shape);
  });
});

describe("fkey", () => {
  test("changes with the icon", () => {
    const base = { id: "b1", status: "idle" as const, browser: { running: true } };
    const blob = fkey({ ...base, face: { shape: "cloud", color: FACE_COLORS[2] } });
    const brand = fkey({ ...base, face: { shape: "cloud", color: FACE_COLORS[2], icon: "github" } });
    const other = fkey({ ...base, face: { shape: "cloud", color: FACE_COLORS[2], icon: "slack" } });
    expect(blob).not.toBe(brand);
    expect(brand).not.toBe(other);
    expect(brand.endsWith(":github")).toBe(true);
  });
});

describe("BRANDS", () => {
  test("every brand has a name, a hex colour and glyph markup", () => {
    for (const [k, b] of Object.entries(BRANDS)) {
      expect(b.name.length).toBeGreaterThan(0);
      expect(b.color).toMatch(/^#[0-9a-f]{6}$/i);
      const g = b.glyph(b.color);
      expect(g.startsWith("<")).toBe(true);
      expect(g.includes("${")).toBe(false);
      expect(k).toMatch(/^[a-z]+$/);
    }
  });
  test("glyphs that cut out shapes use the disc colour", () => {
    expect(BRANDS.youtube.glyph("#abcdef")).toContain('fill="#abcdef"');
  });
});
