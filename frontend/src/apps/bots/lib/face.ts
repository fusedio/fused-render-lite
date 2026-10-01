// Avatar face data (OpenBot core.js): shape and color from bot.face if picked, else hashed from the bot id (stable
// across renames); mood follows status. components/Face.tsx draws it; components/faceAnim.ts animates it.
import type { Bot, Face } from "./api";

export const FACE_SHAPES: Record<string, string> = {
  circle:   "M50 14 C70 14 86 30 86 50 C86 70 70 86 50 86 C30 86 14 70 14 50 C14 30 30 14 50 14 Z",
  oval:     "M42 14 C62 6 84 18 88 40 C91 60 84 86 62 90 C42 94 18 82 12 60 C7 40 22 22 42 14 Z",  /* tilted, sagging blob */
  square:   "M36 16 C46 14 60 14 70 16 C80 18 86 24 85 36 C84 50 84 62 86 74 C87 84 80 88 70 88 C56 88 40 90 26 86 C16 83 12 76 14 66 C16 54 15 42 16 32 C17 22 26 18 36 16 Z",  /* softened, melting box */
  pill:     "M30 28 L70 28 C82 28 90 38 90 50 C90 62 82 72 70 72 L30 72 C18 72 10 62 10 50 C10 38 18 28 30 28 Z",
  triangle: "M44 18 C48 12 52 12 56 18 L86 70 C90 78 86 84 78 84 L22 84 C14 84 10 78 14 70 Z",
  hexagon:  "M44 12 C48 10 52 10 56 12 L82 27 C86 29 88 32 88 36 L88 64 C88 68 86 71 82 73 L56 88 C52 90 48 90 44 88 L18 73 C14 71 12 68 12 64 L12 36 C12 32 14 29 18 27 Z",
  cloud:    "M30 84 C16 84 10 74 12 64 C6 56 10 44 22 42 C22 28 34 20 46 26 C54 14 74 16 78 32 C92 34 94 52 84 58 C90 70 82 84 68 84 Z",
  drop:     "M50 10 C54 22 84 44 84 62 C84 82 68 92 50 92 C32 92 16 82 16 62 C16 44 46 22 50 10 Z",
};
export const FACE_COLORS = ["#eafe68", "#ffffff", "#7a5230", "#d33b3b", "#f0762a", "#f2a232", "#2f8f58", "#2a9a86", "#2f7ae5", "#8a4fe0", "#d33f8e", "#767676"];

export const hash = (name: unknown): number => {
  let h = 0;
  for (const c of String(name || "")) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h;
};

/** What faceOf needs: a bot, or a bare `{face}` (the picker's swatches). */
export type FaceSubject = { id?: string; name?: string; face?: Face | null; status?: Bot["status"]; browser?: Bot["browser"] };

export const faceOf = (b: FaceSubject): { shape: string; color: string } => {
  const h = hash(b.id || b.name), keys = Object.keys(FACE_SHAPES), f = b.face || {};
  return {
    shape: f.shape && FACE_SHAPES[f.shape] ? f.shape : keys[h % keys.length],
    color: f.color && FACE_COLORS.includes(f.color) ? f.color : FACE_COLORS[1 + (h >> 4) % (FACE_COLORS.length - 1)],
  };
};

export type MoodName = "idle" | "running" | "waiting" | "paused" | "error";
export type FaceAction = "look" | "blink" | "bounce" | "gasp" | "snore" | "tear" | "spin";
/** eye: [translateY, rotate (mirrored per eye), scaleX, scaleY]; face: slump [translateY, rotate]; loop: the header routine. */
export interface Mood { eye: [number, number, number, number]; face: [number, number]; loop: [FaceAction, number][] }

// Mood per status: eye pose, face slump, and the header routine.
export const MOODS: Record<MoodName, Mood> = {
  idle:    { eye: [0, 0, 1, 1],        face: [0, 0],  loop: [["look", 900], ["blink", 600], ["look", 1200], ["blink", 300]] },
  running: { eye: [-1, -6, 1.1, 1.1],  face: [0, 0],  loop: [["look", 400], ["blink", 300], ["bounce", 900], ["look", 500], ["blink", 300]] },
  waiting: { eye: [-3, 0, 1.5, 1.3],   face: [0, 0],  loop: [["look", 500], ["gasp", 900], ["blink", 300], ["look", 700]] },
  paused:  { eye: [2, 0, 1.2, .12],    face: [6, 8],  loop: [["snore", 1800], ["snore", 1800]] },
  error:   { eye: [4, 14, 1, .75],     face: [4, -4], loop: [["look", 900], ["blink", 700], ["tear", 1600], ["look", 1100]] },
};

export const moodOf = (b: FaceSubject): MoodName =>
  b.status === "idle" && !b.browser?.running ? "paused" : b.status && b.status in MOODS ? (b.status as MoodName) : "idle";

/** What a drawn face depends on: a change remounts the SVG (and restarts the header routine). */
export const fkey = (b: FaceSubject): string => [b.id, moodOf(b), ...Object.values(faceOf(b))].join(":");

// Excitement props from the emoji builder: four rainbow ribbon arcs that draw on while the face spins, and seven sparks.
export const RIBBONS: { stroke: string; d: string }[] = ([["#ff6fb5", 40], ["#8dff5e", 46], ["#ffb347", 36], ["#5fe0ff", 50]] as [string, number][])
  .map(([c, r]) => ({ stroke: c, d: `M${50 + r * .85} ${50 + r * .4} A${r} ${r} 0 0 0 ${50 - r * .9} ${50 - r * .5}` }));
export const SPARKS = { d: "M0 -4 L1.1 -1.1 L4 0 L1.1 1.1 L0 4 L-1.1 1.1 L-4 0 L-1.1 -1.1 Z", count: 7 };

// A reaction plays on the face (👍 blink, 😂 🎉 bounce, ❤️ spin, 😮 gasp, 👎 tear), then it settles back into its status mood.
export const FACE_RX: Record<string, [MoodName, ...FaceAction[]]> = {
  "👍": ["idle", "blink", "look", "blink"], "😂": ["idle", "bounce", "blink"], "🎉": ["idle", "bounce"],
  "❤️": ["running", "spin"], "😮": ["waiting", "gasp"], "👎": ["error", "tear"],
};

// After a pick the picker preview walks happy → sad → surprised → excited → sleepy, then settles back to happy.
export const PICK_CYCLE: [MoodName, FaceAction][] = [["idle", "blink"], ["error", "tear"], ["waiting", "gasp"], ["running", "spin"], ["paused", "snore"]];
