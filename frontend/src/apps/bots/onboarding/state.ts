// The first-run wizard is a ROUTE (`/onboarding`), not an overlay: bots.tsx
// renders it alone (no bot list, no chat) on that path, the server answers a
// refresh on it and redirects a never-seen install's `/` to it
// (fused_render_app/onboarding.py). Opening and closing are navigations —
// nothing here needs a store. What is left is the rule and the two addresses.
import type { OnboardingState } from "@platform/lib/api";

export const ONBOARDING_PATH = "/onboarding";

/** Where the wizard lets go: the bots page. `/?new=1` additionally opens the
 *  "+ New bot" chooser (apps/bots/App.tsx reads it once). */
export const EXIT_PATH = "/";
export const EXIT_NEW_BOT = "/?new=1";

/** The auto-show rule, from the server's flags: never completed, never
    dismissed AND NEVER OPENED. The server applies the same rule to `/`; this
    is for anything client-side that wants to agree with it. */
export function shouldAutoShow(state: OnboardingState | null | undefined): boolean {
  if (!state) return false;
  return state.completed_at == null && state.dismissed_at == null && state.opened_at == null;
}
