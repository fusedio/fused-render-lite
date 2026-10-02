// The first-run wizard: the `/onboarding` route, four steps, every one of
// them skippable. bots.tsx renders it ALONE on that path — no bot list, no
// chat, no store — and the server redirects a never-seen install's `/` here
// (fused_render_app/onboarding.py). Ported from fused-render's
// shell/onboarding/OnboardingWizard; the differences are FusedBot's:
//
//   1 About        — what a bot is, one screen
//   2 Claude Code  — installed / new enough / signed in / on PATH, with the
//                    install and sign-in buttons
//   3 Chrome       — found in /Applications, or a link to get it
//   4 Models       — the bots' local models (Gemma 4B / 12B), nothing preselected
//   5 First bot    — hands over to the bots page with "+ New bot" open
//
// Steps 1–3 write nothing but their own STAGE STATUS (progress.ts — the pills
// in the bar above; a reopen lands on the first step still to do). Step 4
// starts model downloads only on a click; they are server-owned jobs that
// outlive the wizard. Step 4's "+ New bot" is "complete". ✕ / Escape record
// a DISMISS. Both stop the auto-show.
//
// EXITS ARE FULL NAVIGATIONS, AND THEY WAIT. fused-render's wizard fired its
// complete/dismiss POST and navigated in the same tick, which was safe
// because its auto-show was a client-side rule decided once per page load.
// Here the SERVER redirects `/` while the flags are empty, so a load of `/`
// that lands before the POST does would bounce straight back in. Every way
// out awaits its write, then `location.assign`. The `opened` POST on mount is
// what covers a user who looks and leaves by the brand link, Back or a
// closed window: once it has landed, `/` is the bots page again.
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, X } from "lucide-react";

import { completeOnboarding, dismissOnboarding, openedOnboarding } from "@platform/lib/api";
import { useClaudeSetup } from "@platform/lib/claude-setup";
import { cn } from "@platform/lib/utils";
import { Button } from "@platform/shadcn/ui/button";
import { FusedMark } from "@platform/ui/FusedMark";

import { AboutStep } from "./AboutStep";
import { ChromeStep } from "./ChromeStep";
import { ClaudeStep } from "./ClaudeStep";
import { FirstBotStep } from "./FirstBotStep";
import { forgetModelsStep, ModelsStep, useModelPicks } from "./ModelsStep";
import { firstOpenStage, getProgress, reportStage, setProgress, stageStatus, useOnboardingState } from "./progress";
import { EXIT_NEW_BOT, EXIT_PATH } from "./state";
import "./onboarding.css";

type StepId = "about" | "claude" | "chrome" | "models" | "bot";

const STEPS: { id: StepId; label: string }[] = [
  { id: "about", label: "About" },
  { id: "claude", label: "Claude Code" },
  { id: "chrome", label: "Chrome" },
  { id: "models", label: "Models" },
  { id: "bot", label: "First bot" },
];

// The step id rides in the query so a refresh and a link both land on it.
const STEP_PARAM = "step";
function asStepId(v: string | null | undefined): StepId | null {
  return STEPS.some((s) => s.id === v) ? (v as StepId) : null;
}
function stepFromUrl(): StepId | null {
  return asStepId(new URLSearchParams(location.search).get(STEP_PARAM));
}

export function OnboardingWizard() {
  const [stepId, setStepId] = useState<StepId>(
    () => stepFromUrl() ?? asStepId(firstOpenStage(getProgress()?.stages)) ?? "about",
  );
  const settled = useRef(false);
  const [leaving, setLeaving] = useState(false);
  // Being on screen is the one fact the server needs from a visit. Once per
  // mount.
  useEffect(() => {
    openedOnboarding().then(
      (s) => setProgress(s),
      () => undefined,
    );
  }, []);
  useEffect(() => {
    const url = new URL(location.href);
    if (url.searchParams.get(STEP_PARAM) === stepId) return;
    url.searchParams.set(STEP_PARAM, stepId);
    history.replaceState(history.state, "", url.pathname + url.search);
  }, [stepId]);
  // ONE setup machine for the whole wizard. Focus re-checks only while the
  // Claude step is up.
  const setup = useClaudeSetup(stepId === "claude");
  const picks = useModelPicks();
  const progress = useOnboardingState();
  const stages = progress?.stages;

  const index = Math.max(
    0,
    STEPS.findIndex((s) => s.id === stepId),
  );
  const step = STEPS[index];
  const last = index >= STEPS.length - 1;
  const setIndex = (i: number) => setStepId(STEPS[Math.max(0, Math.min(i, STEPS.length - 1))].id);
  // About has nothing to check: opening it is completing it.
  useEffect(() => {
    if (step.id === "about") reportStage("about", "complete", { viewed_at: Date.now() / 1000 });
  }, [step.id]);
  const eyebrowText = `Step ${index + 1} of ${STEPS.length}`;
  // One yellow button per screen: a step with its own work to do owns the
  // colour while that work is open and Next goes quiet. Reports are TAGGED
  // with the step they came from, so a fresh step never inherits a verdict.
  const [work, setWork] = useState<{ id: string; busy: boolean } | null>(null);
  const stepHasWork = work?.id === step.id && work.busy;
  const onWork = useCallback((busy: boolean) => setWork({ id: step.id, busy }), [step.id]);

  // The flag, then the navigation. At most one write per visit.
  const leave = useCallback(async (how: "complete" | "dismiss", to: string) => {
    if (settled.current) return;
    settled.current = true;
    setLeaving(true);
    if (how === "complete") {
      forgetModelsStep();
      await completeOnboarding().catch(() => undefined);
    } else {
      await dismissOnboarding().catch(() => undefined);
    }
    location.assign(to);
  }, []);
  const dismiss = useCallback(() => void leave("dismiss", EXIT_PATH), [leave]);
  const explore = useCallback(() => void leave("complete", EXIT_PATH), [leave]);
  const newBot = useCallback(() => void leave("complete", EXIT_NEW_BOT), [leave]);

  const next = () => {
    if (last) explore();
    else setIndex(index + 1);
  };
  const back = () => setIndex(index - 1);

  // Escape dismisses; ⌘/Ctrl+Enter advances.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        dismiss();
      } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        next();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `next` is a per-render closure over index
  }, [dismiss, last, index]);

  const eyebrow = (
    <>
      <span>{eyebrowText}</span>
      <div className="flex items-center gap-2 normal-case tracking-normal">
        <Button variant="outline" size="sm" onClick={back} disabled={index === 0 || leaving}>
          <ArrowLeft data-icon="inline-start" />
          Back
        </Button>
        {last ? (
          <Button key="explore" variant="outline" size="sm" onClick={explore} disabled={leaving}>
            I'll explore on my own
          </Button>
        ) : (
          <Button
            key="next"
            variant={stepHasWork ? "outline" : "accent"}
            className={stepHasWork ? undefined : "onboarding-accent"}
            size="sm"
            onClick={next}
            disabled={leaving}
            title="⌘/Ctrl + Enter"
          >
            Next
            <ArrowRight data-icon="inline-end" />
          </Button>
        )}
      </div>
    </>
  );

  return (
    <div className="onboarding flex min-h-screen flex-col bg-background text-foreground" aria-label="Set up FusedBot">
      {/* Top bar: brand · steps · close. Three tracks, the outer two an equal
          `1fr`, so the middle one is centred on the BAR. */}
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 border-b border-border px-5 py-3">
        <a
          href={EXIT_PATH}
          title="Bots"
          onClick={(e) => {
            e.preventDefault();
            dismiss();
          }}
          className="group flex min-w-0 items-center gap-[9px] text-[13.5px] font-[650] tracking-[0.01em] text-foreground no-underline"
        >
          <span className="flex shrink-0 items-center text-[var(--accent)]">
            <FusedMark size={20} />
          </span>
          <span className="truncate sm:max-[760px]:hidden">FusedBot</span>
        </a>

        {/* Every step is a link, in both directions: nothing gates anything. */}
        <ol className="my-0 hidden list-none items-center gap-0.5 rounded-lg bg-muted/60 p-0.5 sm:flex" aria-label="Setup steps">
          {STEPS.map((s, i) => {
            const status = stageStatus(stages, s.id);
            const done = status === "complete";
            const partial = status === "partial";
            const current = i === index;
            const statusWord = done ? "complete" : partial ? "partly done" : "not done";
            return (
              <li key={s.id} className="flex items-center">
                <button
                  type="button"
                  onClick={() => setStepId(s.id)}
                  aria-current={current ? "step" : undefined}
                  title={`${s.label} — ${statusWord}`}
                  className={cn(
                    "flex cursor-pointer appearance-none items-center gap-1.5 rounded-md border-0 bg-transparent px-3 py-1.5 text-xs leading-none [font-family:inherit] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    current ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span
                    className={cn(
                      "grid size-4 place-items-center rounded-full text-[10px] font-semibold tabular-nums",
                      done && "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
                      partial && "bg-amber-500/15 text-amber-600 dark:text-amber-400",
                      !done && !partial && current && "bg-foreground text-background",
                      !done && !partial && !current && "bg-muted-foreground/15 text-muted-foreground",
                    )}
                    aria-hidden
                  >
                    {done ? (
                      <Check className="size-2.5" strokeWidth={3} />
                    ) : partial ? (
                      <span className="size-2 rounded-full border-[1.5px] border-current [background:linear-gradient(90deg,currentColor_50%,transparent_50%)]" />
                    ) : (
                      i + 1
                    )}
                  </span>
                  {s.label}
                </button>
              </li>
            );
          })}
        </ol>

        <button
          type="button"
          onClick={dismiss}
          aria-label="Close setup"
          title="Skip for now"
          className="col-start-3 grid size-8 cursor-pointer justify-self-end appearance-none place-items-center rounded-md border-0 bg-transparent text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-6 py-10">
          {step.id === "about" && <AboutStep eyebrow={eyebrow} />}
          {step.id === "claude" && <ClaudeStep setup={setup} eyebrow={eyebrow} onWork={onWork} />}
          {step.id === "chrome" && <ChromeStep eyebrow={eyebrow} onWork={onWork} />}
          {step.id === "models" && <ModelsStep picks={picks} eyebrow={eyebrow} onWork={onWork} />}
          {step.id === "bot" && <FirstBotStep eyebrow={eyebrow} onNewBot={newBot} busy={leaving} />}
        </div>
      </div>
    </div>
  );
}

export default OnboardingWizard;
