import { describe, expect, test } from "bun:test";
import { describeUpdate, HOT_WINDOW_MS, POLL_BUSY_MS, POLL_HOT_MS, POLL_IDLE_MS, pollDelay, type UpdateStatus } from "./update";

const base: UpdateStatus = {
  state: "idle", current_version: "0.11.0", latest_version: null, progress: null, progress_total: null,
  phase: null, error: null, check_only: false, check_error: null,
};
const st = (over: Partial<UpdateStatus>): UpdateStatus => ({ ...base, ...over });

describe("describeUpdate", () => {
  const rows: [string, UpdateStatus | null, ReturnType<typeof describeUpdate>][] = [
    ["no updater (null)", null, null],
    ["idle", st({}), null],
    ["idle after a failed check", st({ check_error: "timed out" }), null],
    ["checking", st({ state: "checking" }), null],
    ["available", st({ state: "available", latest_version: "0.12.0" }), {
      tone: "info", text: "FusedBot 0.12.0 is available", sub: "You have 0.11.0",
      action: { kind: "install", label: "Update" }, progress: null }],
    ["available, check-only dev manager", st({ state: "available", latest_version: "0.12.0", check_only: true }), {
      tone: "info", text: "FusedBot 0.12.0 is available", sub: "Dev run: updates install only in the packaged app",
      action: null, progress: null }],
    ["downloading, size known", st({ state: "installing", phase: "downloading", latest_version: "0.12.0", progress: 1048576 * 20, progress_total: 1048576 * 80 }), {
      tone: "info", text: "Downloading FusedBot 0.12.0", sub: "20.0 MB of 80.0 MB",
      action: { kind: "cancel", label: "Cancel" }, progress: { fraction: 0.25 } }],
    ["downloading, size unknown", st({ state: "installing", phase: "downloading", latest_version: "0.12.0", progress: 1048576 * 3 }), {
      tone: "info", text: "Downloading FusedBot 0.12.0", sub: "3.0 MB",
      action: { kind: "cancel", label: "Cancel" }, progress: { fraction: null } }],
    ["downloading, first byte not in yet", st({ state: "installing", phase: "downloading", latest_version: "0.12.0", progress: 0 }), {
      tone: "info", text: "Downloading FusedBot 0.12.0", sub: "",
      action: { kind: "cancel", label: "Cancel" }, progress: { fraction: null } }],
    ["installing (swap, not cancellable)", st({ state: "installing", phase: "installing", latest_version: "0.12.0" }), {
      tone: "info", text: "Installing FusedBot 0.12.0…", sub: "", action: null, progress: { fraction: null } }],
    ["installed", st({ state: "installed", latest_version: "0.12.0" }), {
      tone: "info", text: "FusedBot 0.12.0 is installed", sub: "Restart to finish",
      action: { kind: "relaunch", label: "Restart FusedBot" }, progress: null }],
    ["install failed", st({ state: "error", latest_version: "0.12.0", error: "not enough free disk space to download the update" }), {
      tone: "err", text: "Update failed: not enough free disk space to download the update", sub: "",
      action: { kind: "retry", label: "Retry" }, progress: null }],
    ["install failed, no message", st({ state: "error", latest_version: "0.12.0" }), {
      tone: "err", text: "Update failed", sub: "", action: { kind: "retry", label: "Retry" }, progress: null }],
  ];
  for (const [name, input, want] of rows) test(name, () => expect(describeUpdate(input)).toEqual(want));

  test("a download fraction is clamped to [0, 1]", () => {
    const v = describeUpdate(st({ state: "installing", phase: "downloading", progress: 120, progress_total: 100 }));
    expect(v?.progress).toEqual({ fraction: 1 });
  });
});

describe("pollDelay", () => {
  const late = HOT_WINDOW_MS + 1;
  test("hot for the first 20 s, whatever the server said", () => {
    expect(pollDelay(null, 0)).toBe(POLL_HOT_MS);
    expect(pollDelay(st({}), HOT_WINDOW_MS - 1)).toBe(POLL_HOT_MS);
  });
  test("busy while a check or install runs, at any age", () => {
    expect(pollDelay(st({ state: "installing", phase: "downloading" }), late)).toBe(POLL_BUSY_MS);
    expect(pollDelay(st({ state: "checking" }), late)).toBe(POLL_BUSY_MS);
  });
  test("idle cadence afterwards", () => {
    for (const state of ["idle", "available", "installed", "error"] as const) expect(pollDelay(st({ state }), late)).toBe(POLL_IDLE_MS);
  });
  test("no updater past the hot window: stop", () => {
    expect(pollDelay(null, late)).toBeNull();
  });
});
