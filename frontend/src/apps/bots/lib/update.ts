// Render App's self-update, read off GET /api/update (fused_render_app/update/mac.py's UpdateManager.status()).
// Pure on purpose: describeUpdate() is the whole state -> banner decision and pollDelay() the whole cadence, so
// both are table-tested (update.test.ts) and UpdateBanner.tsx only owns the timer and the clicks. No top-level
// window/document here, so bun imports it bare.
//
// The manager's states (UpdateManager docstring): idle -> checking -> (idle | available) -> installing(progress)
// -> installed | error(message). "installing" carries a phase: "downloading" while the DMG streams (cancellable),
// "installing" from the mount to the bundle swap (not cancellable; mac.py cancel() ignores it there).
import { fmtBytes } from "./format";
import { request } from "./api";

export type UpdateState = "idle" | "checking" | "available" | "installing" | "installed" | "error";

/** UpdateManager.status(), field for field. */
export interface UpdateStatus {
  state: UpdateState;
  current_version: string;
  latest_version: string | null;
  /** Bytes downloaded so far (a float on the wire); null outside the download. */
  progress: number | null;
  /** The DMG's size when the server sent Content-Length; null when unknown. */
  progress_total: number | null;
  /** Only while state is "installing". */
  phase: "downloading" | "installing" | null;
  /** The install's failure (state "error"). */
  error: string | null;
  /** The dev-run manager (FUSED_RENDER_APP_UPDATE_DEV_MANAGER): checks for real, refuses to install. */
  check_only: boolean;
  /** The last CHECK's failure. Routine (offline, a blip) and the state stays where it was, so the banner ignores it. */
  check_error: string | null;
}

export type UpdateActionKind = "install" | "cancel" | "relaunch" | "retry";
export interface UpdateAction { kind: UpdateActionKind; label: string }

export interface UpdateView {
  tone: "info" | "err";
  text: string;
  /** Secondary text after the sentence; "" for none. */
  sub: string;
  action: UpdateAction | null;
  /** null: no bar. fraction null: indeterminate. */
  progress: { fraction: number | null } | null;
}

const NAME = "Browser Bots";

/** What the banner shows for a status, or null for nothing (no updater, idle, up to date, checking). */
export function describeUpdate(s: UpdateStatus | null | undefined): UpdateView | null {
  if (!s) return null;
  const v = s.latest_version ? `${NAME} ${s.latest_version}` : NAME;
  switch (s.state) {
    case "available":
      return {
        tone: "info",
        text: s.latest_version ? `${v} is available` : `A ${NAME} update is available`,
        sub: s.check_only ? "Dev run: updates install only in the packaged app" : `You have ${s.current_version}`,
        // install() refuses on the check-only manager, so no button there.
        action: s.check_only ? null : { kind: "install", label: "Update" },
        progress: null,
      };
    case "installing": {
      if (s.phase === "downloading") {
        const done = s.progress ?? 0, total = s.progress_total;
        const known = total != null && total > 0;
        return {
          tone: "info",
          text: `Downloading ${v}`,
          sub: known ? `${fmtBytes(done)} of ${fmtBytes(total)}` : done > 0 ? fmtBytes(done) : "",
          action: { kind: "cancel", label: "Cancel" },
          progress: { fraction: known ? Math.min(1, Math.max(0, done / total)) : null },
        };
      }
      // phase "installing" (mount, verify, swap). Progress is reset to null there.
      return { tone: "info", text: `Installing ${v}…`, sub: "", action: null, progress: { fraction: null } };
    }
    case "installed":
      return {
        tone: "info",
        text: s.latest_version ? `${v} is installed` : `${NAME} is updated`,
        sub: "Restart to finish",
        action: { kind: "relaunch", label: `Restart ${NAME}` },
        progress: null,
      };
    case "error":
      return {
        tone: "err",
        text: s.error ? `Update failed: ${s.error}` : "Update failed",
        sub: "",
        // Retry re-runs install(), which mac.py allows from "error" (a non-forced /check is a no-op there).
        action: { kind: "retry", label: "Retry" },
        progress: null,
      };
    default:
      return null; // idle, checking
  }
}

// ------------------------------------------------------------------ cadence ----
export const POLL_HOT_MS = 2_000;
export const HOT_WINDOW_MS = 20_000;
export const POLL_IDLE_MS = 60_000;
export const POLL_BUSY_MS = 2_000;

/** ms until the next GET, or null to stop polling for the session.
 *  `update` null means no manager runs here (dev server). macapp starts the manager before it opens the window,
 *  so a null that outlives the hot window is null for good. */
export function pollDelay(s: UpdateStatus | null, sinceStartMs: number): number | null {
  if (s?.state === "installing" || s?.state === "checking") return POLL_BUSY_MS;
  if (sinceStartMs < HOT_WINDOW_MS) return POLL_HOT_MS;
  if (!s) return null;
  return POLL_IDLE_MS;
}

// ------------------------------------------------------------------ routes ----
// Every POST carries X-Fused through request(). All four answer status() except relaunch ({relaunching: true}).
export const updateApi = {
  status: () => request<{ update: UpdateStatus | null }>("GET", "/api/update", undefined, "update"),
  install: (expected_version: string | null) =>
    request<UpdateStatus>("POST", "/api/update/install", expected_version ? { expected_version } : {}, "update/install"),
  cancel: () => request<UpdateStatus>("POST", "/api/update/cancel", {}, "update/cancel"),
  relaunch: () => request<{ relaunching: true }>("POST", "/api/update/relaunch", {}, "update/relaunch"),
};
