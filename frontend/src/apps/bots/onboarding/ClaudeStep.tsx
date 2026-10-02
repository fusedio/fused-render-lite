import { useEffect, type ReactNode } from "react";
// Step 2 — Claude Code and Chrome: what a bot needs on this Mac. A CHECKLIST,
// ported from fused-render's wizard step: the four Claude facts are rows,
// each done (struck through, green check) or open (the health strip's own
// IssueRow attached — same buttons, same endpoints, same polls, via
// lib/claude-setup), plus one row for Chrome, which fused-render never
// needed and every bot here does.
//
// THIS IS THE STEP WITH THE PUSH. FusedBot wants bots on Claude Code: the
// copy asks for it, Install and Sign in are the yellow buttons, and the
// wizard says plainly what skipping means — every preset bot is set to a
// Claude model, and a user who does not want Claude Code changes a bot's
// Model to a local one in that bot's Settings, by hand. Nothing is switched
// for them (owner's call, 2026-10-02). Next is never blocked all the same:
// a wizard that cannot be left is worse than one that is skipped.
import { Check, Circle, Minus, RefreshCw } from "lucide-react";

import type { ClaudeHealth } from "@platform/lib/api";
import { claudeIssues, type ClaudeIssue } from "@platform/lib/claude-health";
import type { ClaudeSetup } from "@platform/lib/claude-setup";
import { Button } from "@platform/shadcn/ui/button";
import { Skeleton } from "@platform/shadcn/ui/skeleton";
import { IssueRow } from "@platform/ui/ClaudeHealthStrip";

import { reportStage, useOnboardingState, type StageStatus } from "./progress";
import { StepHeader } from "./StepHeader";

type RowState = "done" | "open" | "unknown";

interface Row {
  id: "installed" | "version" | "signed-in" | "path";
  label: string;
  hint: string;
  optional?: boolean;
  state: RowState;
  /** Which strip issues belong to this row — the first present one renders. */
  issueIds: ClaudeIssue["id"][];
}

// WHO is signed in, when the CLI said.
function signedInHint(h: ClaudeHealth): string {
  const a = h.account;
  if (h.signed_in !== true || !a) return "Uses your existing Claude subscription — no key to paste.";
  if (a.method === "apiKey") return "Using an API key from the environment (ANTHROPIC_API_KEY).";
  if (a.email) {
    const where = [a.org, a.plan].filter(Boolean).join(" · ");
    return `Signed in as ${a.email}${where ? ` (${where})` : ""}.`;
  }
  if (a.method === "oauthToken") return "Using an OAuth token from the environment.";
  return a.method ? `Signed in via ${a.method}.` : "Signed in.";
}

function rowsFor(h: ClaudeHealth): Row[] {
  const runnable = h.found && !h.broken;
  return [
    {
      id: "installed",
      label: "Claude Code installed",
      hint: "The `claude` command-line tool, from Anthropic. Every preset bot runs on it.",
      state: runnable ? "done" : "open",
      issueIds: ["missing", "unusable-override", "broken"],
    },
    {
      id: "version",
      label: `Version ${h.min_version} or newer`,
      hint: h.version ? `Found ${h.version}.` : "Could not read the version.",
      state: !runnable ? "open" : h.version == null ? "unknown" : h.outdated ? "open" : "done",
      issueIds: ["outdated"],
    },
    {
      id: "signed-in",
      label: "Signed in",
      hint: signedInHint(h),
      state: !runnable ? "open" : h.signed_in === true ? "done" : h.signed_in === false ? "open" : "unknown",
      issueIds: ["signed-out"],
    },
    {
      id: "path",
      label: "Available in your terminal",
      hint: "Optional. Bots work either way; this is for typing `claude` yourself.",
      optional: true,
      state: !runnable ? "open" : h.on_shell_path === false ? "open" : h.on_shell_path === true ? "done" : "unknown",
      issueIds: ["not-on-path"],
    },
  ];
}

function StateIcon({ state, optional }: { state: RowState; optional?: boolean }) {
  if (state === "done")
    return (
      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
        <Check className="size-3.5" strokeWidth={3} />
      </span>
    );
  if (state === "unknown" || optional)
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

const CHROME_URL = "https://www.google.com/chrome/";

/** The Chrome row: the server's probe (onboarding.py chrome_snapshot, the
 *  bots' own candidate list), re-read on every return to the tab — which is
 *  how a user who went to install Chrome sees the row turn green. */
function ChromeRow() {
  const chrome = useOnboardingState()?.chrome;
  const state: RowState = chrome?.found === true ? "done" : chrome?.found === false ? "open" : "unknown";
  useEffect(() => {
    if (chrome?.found === true) reportStage("chrome", "complete", { path: chrome.path });
    else if (chrome?.found === false) reportStage("chrome", "pending", { path: null });
  }, [chrome?.found, chrome?.path]);
  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-start gap-3">
        <StateIcon state={state} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">
            <span className={state === "done" ? "text-muted-foreground line-through" : undefined}>Google Chrome installed</span>
          </div>
          <div className="text-xs text-muted-foreground">
            {state === "done"
              ? `Found at ${chrome?.path}. Each bot opens its own window of it.`
              : state === "open"
                ? "No Chrome, Chromium, Edge or Brave in /Applications. A bot cannot start without one."
                : "Couldn't check — carry on, a bot will say so if it cannot find one."}
          </div>
        </div>
      </div>
      {state === "open" && (
        <div className="ml-9 flex flex-wrap items-center gap-2">
          <a className="claude-health-action" href={CHROME_URL} target="_blank" rel="noreferrer">
            Get Chrome
          </a>
          <span className="text-xs text-muted-foreground">Install it, come back to this window and the row updates.</span>
        </div>
      )}
    </li>
  );
}

// `setup` is the wizard's single machine (OnboardingWizard owns it).
export function ClaudeStep({
  setup,
  eyebrow,
  onWork,
}: {
  setup: ClaudeSetup;
  eyebrow: ReactNode;
  onWork: (busy: boolean) => void;
}) {
  const { health, loaded, busy, load } = setup;
  const issues = claudeIssues(health);
  const rows = health ? rowsFor(health) : null;
  // OPTIONAL ROWS ARE NOT WORK: "done" means every REQUIRED row is done.
  const required = rows?.filter((r) => !r.optional);
  const allDone = required?.every((r) => r.state === "done");
  const optionalOpen = rows?.some((r) => r.optional && r.state === "open");
  // While a required row has a button of its own to press, Next is not the
  // yellow one.
  const anyActionable = required?.some(
    (r) => r.state === "open" && issues.some((i) => r.issueIds.includes(i.id)),
  );
  useEffect(() => onWork(anyActionable === true), [onWork, anyActionable]);
  // THE STAGE: complete when every required row is done; partial when the
  // CLI runs but a required row is open or unknown; pending when it does not
  // run at all. Nothing until health has answered.
  const runnable = health ? health.found && !health.broken : false;
  const stage: StageStatus | null = !health ? null : allDone ? "complete" : runnable ? "partial" : "pending";
  useEffect(() => {
    if (!health || !stage) return;
    reportStage("claude", stage, {
      version: health.version,
      outdated: health.outdated,
      signed_in: health.signed_in,
      account: health.account?.email ?? health.account?.method ?? null,
      on_shell_path: health.on_shell_path,
    });
  }, [stage, health]);

  return (
    <div className="flex flex-col gap-6">
      <StepHeader
        eyebrow={eyebrow}
        title="Install Claude Code"
        lead={
          <>
            Bots think with Claude Code running on this Mac — it is what reads the page, decides the
            next click and writes the reply, on your existing Claude subscription. Every preset bot is
            set to a Claude model, so this is the one thing to set up. Chrome is the other: each bot
            drives its own window of it.
          </>
        }
      />

      <div className="flex items-center justify-between gap-4">
        <p className="m-0 text-sm text-muted-foreground" role="status">
          {!loaded
            ? "Checking this Mac…"
            : anyActionable
              ? "A few things still need doing — the open rows have buttons."
              : allDone
                ? optionalOpen
                  ? "Claude Code is ready. The last row is optional."
                  : "Everything is in place."
                : "Nothing here will block you — carry on."}
        </p>
        <Button variant="outline" size="sm" onClick={() => load(true)} disabled={busy}>
          <RefreshCw data-icon="inline-start" className={busy ? "animate-spin" : undefined} />
          {busy ? "Checking…" : "Check again"}
        </Button>
      </div>

      <ol className="m-0 flex list-none flex-col divide-y divide-border rounded-xl border border-border bg-card p-0">
        {!rows &&
          [0, 1, 2, 3].map((i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-3">
              <Skeleton className="size-6 rounded-full" />
              <Skeleton className="h-4 w-48" />
            </li>
          ))}
        {rows?.map((row) => {
          const issue = row.state === "open" ? issues.find((i) => row.issueIds.includes(i.id)) : undefined;
          return (
            <li key={row.id} className="flex flex-col gap-2 px-4 py-3">
              <div className="flex items-start gap-3">
                <StateIcon state={row.state} optional={row.optional && row.state !== "done"} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <span className={row.state === "done" ? "text-muted-foreground line-through" : undefined}>
                      {row.label}
                    </span>
                    {row.optional && (
                      <span className="rounded-full border border-border px-1.5 py-px text-[11px] font-normal text-muted-foreground">
                        optional
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {row.state === "unknown" ? "Couldn't check — carry on, it will not block you." : row.hint}
                  </div>
                </div>
              </div>
              {issue && (
                <ul className="claude-health-issues onboarding-issue ml-9">
                  <IssueRow
                    issue={issue}
                    install={setup.install}
                    login={setup.login}
                    doctor={issue.id === "broken" ? setup.doctor : null}
                    onAct={setup.act}
                    onCancelLogin={setup.cancelLogin}
                    busy={setup.acting}
                    actionError={issue.action ? setup.actionError : null}
                    doneNote={issue.id === "not-on-path" ? setup.linkedNote : null}
                  />
                </ul>
              )}
            </li>
          );
        })}
        <ChromeRow />
      </ol>

      {/* What skipping means, said once and plainly: the bots do not switch
          model on their own (bot.py `_engine_for` changes the ENGINE when the
          CLI is missing, never the model), so a bot left on a Claude model
          without Claude Code fails its first task. The way out is the bot's
          own Settings. */}
      {loaded && !runnable && (
        <p className="m-0 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-xs leading-relaxed text-foreground">
          <strong>Without Claude Code, a bot on a Claude model cannot run.</strong> If you would rather not
          install it, open each bot's Settings (its avatar, or the ☰ menu) and change its Model to Gemma 4B
          or 12B, the local models on the next screen. FusedBot does not switch a bot's model for you.
        </p>
      )}
    </div>
  );
}
