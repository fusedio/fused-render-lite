// Onboarding PROGRESS — one store for every surface that says how far setup
// has got: the wizard's top-bar pills and the steps themselves, which are the
// writers. Ported from fused-render's shell/onboarding/progress.ts; FusedBot
// has no sidebar meter, so what is left is the pills, the "first step still
// to do" rule, and the merge rules that keep two stage POSTs from racing.
//
// THE MODEL. Every wizard step is a STAGE with a status the step decides for
// itself, from the facts it already checks to draw its own rows:
//
//   stage    pending                partial                      complete
//   about    not yet opened         —                            opened once
//   claude   not installed/unknown  runs, but outdated/signed    installed, new
//                                   out/unknown                  enough, signed in
//   chrome   no Chrome found        —                            Chrome found
//   models   none fetched, none     none here, one or more       one or more
//            downloading            downloading                  models here
//   bot      no bot yet             —                            a bot exists
//
// plus `n/a` for a stage this machine does not have (none today on a
// macOS-only build; kept for the server's closed set).
//
// The status is written to the server (fused_render_app/onboarding.py
// `stages`) with a free-form `meta` note for reference, and read back from
// GET /api/onboarding like the flags. Server-side, not localStorage: every
// port is a new origin. The server OVERRULES the stored status for the stages
// it can see cheaply (Claude from its health cache, Chrome, local models on
// disk, a bot in the data dir), so a bot made without the wizard moves the
// pill too.
import { useSyncExternalStore } from "react";

import {
  getOnboarding,
  setOnboardingStage,
  type OnboardingStage,
  type OnboardingStageStatus,
  type OnboardingState,
} from "@platform/lib/api";

import { ONBOARDING_PATH } from "./state";

export type StageStatus = OnboardingStageStatus;
export type Stages = Record<string, OnboardingStage>;

/** The stage ids, in wizard order — the server's closed set (STEPS). */
export const STAGE_IDS = ["about", "claude", "chrome", "models", "bot"] as const;
export type StageId = (typeof STAGE_IDS)[number];

let snapshot: OnboardingState | null = null;
const listeners = new Set<() => void>();
let inflight: Promise<void> | null = null;

function emit() {
  for (const l of listeners) l();
}

function set(next: OnboardingState | null) {
  if (next === snapshot) return;
  snapshot = next;
  emit();
}

// EVERY REPLY IS THE WHOLE STATE, AND REPLIES DO NOT ARRIVE IN ORDER. Two
// stage POSTs in one tick, or a mount-time GET racing the last write the
// wizard made — whichever lands LAST would win, and a stale one would put a
// stage back to what it was. So a reply is MERGED, not adopted:
//   * flags (completed/dismissed/opened) only ever gain a value;
//   * each stage keeps whichever record is NEWER by `updated_at`. An optimistic
//     local write stamps `now`; the server's own stamp for that write is later
//     still, so the reply replaces it, while a reply from BEFORE the write
//     leaves the local record standing. A server observation carries the
//     stored record's stamp (or null) — "not newer than anything the wizard
//     has written since".
function merge(reply: OnboardingState): void {
  const cur = snapshot;
  if (!cur) return set(reply);
  const stages: Stages = { ...(reply.stages ?? {}) };
  for (const [id, mine] of Object.entries(cur.stages ?? {})) {
    const theirs = stages[id];
    if (!theirs || (mine.updated_at ?? -1) > (theirs.updated_at ?? -1)) stages[id] = mine;
  }
  set({
    ...reply,
    completed_at: reply.completed_at ?? cur.completed_at,
    dismissed_at: reply.dismissed_at ?? cur.dismissed_at,
    opened_at: reply.opened_at ?? cur.opened_at,
    stages,
  });
}

/** Adopt a snapshot some other call brought back (the wizard's `opened`
 *  write, complete, dismiss, the entry's pre-render fetch). Merged. */
export function setProgress(next: OnboardingState): void {
  merge(next);
}

/** Re-read the server. A failed fetch keeps what we have. */
export function refreshProgress(): Promise<void> {
  if (inflight) return inflight;
  inflight = getOnboarding()
    .then((s) => merge(s))
    .catch(() => undefined)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function getProgress(): OnboardingState | null {
  return snapshot;
}

// The server overrules stored statuses with what it can observe, but only on
// a read — so, while anyone is subscribed, re-read on every return to the tab
// (the moment a user comes back from installing Chrome or signing in).
const onVisible = () => {
  if (document.visibilityState === "visible") void refreshProgress();
};

export function subscribeProgress(cb: () => void): () => void {
  listeners.add(cb);
  if (listeners.size === 1) {
    void refreshProgress();
    document.addEventListener("visibilitychange", onVisible);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0) document.removeEventListener("visibilitychange", onVisible);
  };
}

export function useOnboardingState(): OnboardingState | null {
  return useSyncExternalStore(subscribeProgress, getProgress, getProgress);
}

// What each stage last wrote — so a step reporting the same status on every
// poll (Models, every few seconds) writes once, not on every tick.
const lastWritten = new Map<string, StageStatus>();

/** A step reports its status. Fire-and-forget; the server's reply is the new
 *  snapshot. `force` re-sends even when the status has not changed. */
export function reportStage(
  stage: StageId,
  status: StageStatus,
  meta?: Record<string, unknown>,
  force = false,
): void {
  if (!force && lastWritten.get(stage) === status && snapshot?.stages?.[stage]?.status === status) return;
  lastWritten.set(stage, status);
  // Optimistic: the pills should move as the step does, not a round trip later.
  if (snapshot) {
    const prev = snapshot.stages?.[stage];
    set({
      ...snapshot,
      stages: {
        ...(snapshot.stages ?? {}),
        [stage]: { status, meta: { ...(prev?.meta ?? {}), ...(meta ?? {}) }, updated_at: Date.now() / 1000 },
      },
    });
  }
  setOnboardingStage(stage, status, meta).then(
    (s) => merge(s),
    () => undefined,
  );
}

export function stageStatus(stages: Stages | undefined, id: string): StageStatus {
  return stages?.[id]?.status ?? "pending";
}

/** The first stage that still needs doing (not complete, not `n/a`), in
 *  wizard order — where a reopen should land. Null when nothing is left. */
export function firstOpenStage(stages: Stages | undefined): StageId | null {
  for (const id of STAGE_IDS) {
    const s = stageStatus(stages, id);
    if (s !== "complete" && s !== "n/a") return id;
  }
  return null;
}

/** The wizard's URL, open on the first step still to do. Nothing left (or
 *  nothing known) opens at the top. */
export function onboardingUrl(stages: Stages | undefined): string {
  const open = firstOpenStage(stages);
  return open && open !== STAGE_IDS[0] ? `${ONBOARDING_PATH}?step=${encodeURIComponent(open)}` : ONBOARDING_PATH;
}
