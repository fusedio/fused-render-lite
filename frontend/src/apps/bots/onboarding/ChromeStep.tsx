import { useEffect, type ReactNode } from "react";
// Step 3 — Chrome. fused-render's wizard never had this step; every bot here
// needs it: a bot IS a Chrome window of its own (bots/browser.py walks
// CHROME_CANDIDATES when the bot starts and raises when none exists). One
// row, from the server's probe (onboarding.py chrome_snapshot — the same
// candidate list, so this step and the bot cannot disagree), re-read on
// every return to the tab, which is how a user who went to install Chrome
// sees the row turn green. Never blocks Next.
import { Check, Circle, ExternalLink, Minus } from "lucide-react";

import { reportStage, useOnboardingState } from "./progress";
import { StepHeader } from "./StepHeader";

const CHROME_URL = "https://www.google.com/chrome/";

type RowState = "done" | "open" | "unknown";

function StateIcon({ state }: { state: RowState }) {
  if (state === "done")
    return (
      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
        <Check className="size-3.5" strokeWidth={3} />
      </span>
    );
  if (state === "unknown")
    return (
      <span className="grid size-6 shrink-0 place-items-center rounded-full border border-border text-muted-foreground">
        <Minus className="size-3" />
      </span>
    );
  return (
    <span className="grid size-6 shrink-0 place-items-center rounded-full border border-amber-500/50 text-amber-600 dark:text-amber-400">
      <Circle className="size-2.5 fill-current" />
    </span>
  );
}

export function ChromeStep({ eyebrow, onWork }: { eyebrow: ReactNode; onWork: (busy: boolean) => void }) {
  const chrome = useOnboardingState()?.chrome;
  const state: RowState = chrome?.found === true ? "done" : chrome?.found === false ? "open" : "unknown";
  useEffect(() => {
    if (chrome?.found === true) reportStage("chrome", "complete", { path: chrome.path });
    else if (chrome?.found === false) reportStage("chrome", "pending", { path: null });
  }, [chrome?.found, chrome?.path]);
  // "Get Chrome" is this step's own button while Chrome is missing; Next
  // goes quiet until then.
  useEffect(() => onWork(state === "open"), [onWork, state]);

  return (
    <div className="flex flex-col gap-6">
      <StepHeader
        eyebrow={eyebrow}
        title="Chrome"
        lead="Every bot drives its own Chrome window: its own profile, logins, cookies and downloads, separate from yours. FusedBot looks for Google Chrome first, then Chromium, Edge or Brave in /Applications. A bot cannot start without one."
      />

      <p className="m-0 text-sm text-muted-foreground" role="status">
        {state === "done"
          ? "Chrome is here."
          : state === "open"
            ? "No browser found. Install one, come back to this window and the row updates."
            : "Checking this Mac…"}
      </p>

      <ol className="m-0 flex list-none flex-col rounded-xl border border-border bg-card p-0">
        <li className="flex flex-col gap-2 px-4 py-3">
          <div className="flex items-start gap-3">
            <StateIcon state={state} />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">
                <span className={state === "done" ? "text-muted-foreground line-through" : undefined}>Google Chrome installed</span>
              </div>
              <div className="text-xs text-muted-foreground">
                {state === "done"
                  ? `Found at ${chrome?.path}.`
                  : state === "open"
                    ? "Nothing in /Applications that a bot can drive."
                    : "Couldn't check — carry on, a bot will say so if it cannot find one."}
              </div>
            </div>
          </div>
          {state === "open" && (
            <div className="ml-9 flex flex-wrap items-center gap-2">
              <a className="claude-health-action" href={CHROME_URL} target="_blank" rel="noreferrer">
                Get Chrome
                <ExternalLink className="ml-1 inline size-3.5 align-[-2px]" />
              </a>
              <span className="text-xs text-muted-foreground">Opens google.com/chrome in your browser.</span>
            </div>
          )}
        </li>
      </ol>
    </div>
  );
}
