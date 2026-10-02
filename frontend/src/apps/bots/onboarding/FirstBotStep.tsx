import type { ReactNode } from "react";
// Step 4 — the first bot. The bots page already has the whole flow ("+ New
// bot" → preset chooser → bot dialog), and it needs the store, the dialogs
// host and the poll loop, none of which the wizard mounts. So this step is a
// hand-over: "+ New bot" completes the wizard and lands on `/?new=1`, which
// apps/bots/App.tsx reads once and opens the chooser. The `bot` stage is the
// server's to mark (onboarding.py observes the bots data dir), so a bot made
// from that chooser — or any other way — ticks it without this step's help.
import { Bot } from "lucide-react";

import { Button } from "@platform/shadcn/ui/button";

import { useOnboardingState } from "./progress";
import { StepHeader } from "./StepHeader";

export function FirstBotStep({
  eyebrow,
  onNewBot,
  busy,
}: {
  eyebrow: ReactNode;
  /** Complete the wizard and open the "+ New bot" chooser. */
  onNewBot: () => void;
  busy: boolean;
}) {
  const stages = useOnboardingState()?.stages;
  const claudeDone = stages?.claude?.status === "complete";
  const hasBot = stages?.bot?.status === "complete";
  return (
    <div className="flex flex-col gap-6">
      <StepHeader
        eyebrow={eyebrow}
        title="Make your first bot"
        lead="Pick a site it should know — Gmail, Google Sheets, GitHub, Amazon, Hacker News — or start blank and give it standing instructions. It opens its own Chrome window and waits for a task."
      />

      <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5">
        <div className="flex items-center gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-muted">
            <Bot className="size-5" />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-medium">{hasBot ? "You already have a bot" : "No bots yet"}</div>
            <div className="text-xs text-muted-foreground">
              {hasBot
                ? "Make another, or go to the bots page and give it a task."
                : "The chooser opens on the bots page; name and rules come next."}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="accent" className="onboarding-accent" onClick={onNewBot} disabled={busy}>
            + New bot
          </Button>
          {!claudeDone && (
            <span className="text-xs text-muted-foreground">
              Claude Code is not set up: a preset bot will not run until it is, or until you change its Model in Settings.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
