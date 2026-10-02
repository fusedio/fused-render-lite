import type { ReactNode } from "react";
// Step 3 — the bots' local models, and a head start on downloading them.
//
// Adapted from fused-render's Models step with one deliberate difference:
// the offer is the bots' own `LOCAL_MODELS` table (bot.py: Gemma 4B and
// 12B), served by GET /api/onboarding/models with fit.py's verdict for this
// Mac — NOT the AI catalog's `recommended` row, which names a model no bot
// uses. And NOTHING STARTS CHECKED: the user asked that FusedBot never spend
// a download or pick a model for them (2026-10-02). A model is fetched when
// its box is ticked and Download pressed, and otherwise the first task of a
// bot set to it asks in chat (bot.py `_ensure_model_ready`).
//
// What the step buys is the same timing win: the multi-GB wait happens
// while the user makes their first bot instead of inside its first task.
// So NOTHING BLOCKS. Download is fire-and-forget (`supervisor.load(...,
// weights_only=True)` returns the instant its thread is up), Next stays
// live, and leaving the wizard leaves the fetch running — a server-owned
// job, drawn in the download manager like every other one.
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, HardDrive } from "lucide-react";

import { downloadAiModel, getOnboardingModels, type OnboardingModelPick } from "@platform/lib/api";
import { fitNote } from "@platform/lib/fitNote";
import { formatSize } from "@platform/lib/format";
import {
  fetchJobs,
  isRunning,
  isTerminal,
  jobAmount,
  jobFraction,
  jobStatusLine,
  pollInterval,
  type Job,
} from "@platform/lib/jobs";
import { modelSizeLabel } from "@platform/lib/modelSize";
import { Button } from "@platform/shadcn/ui/button";
import { Checkbox } from "@platform/shadcn/ui/checkbox";
import { Skeleton } from "@platform/shadcn/ui/skeleton";

import { reportStage } from "./progress";
import { StepHeader } from "./StepHeader";

/** `null` while the server is still answering. */
export type ModelPicks = OnboardingModelPick[] | null;

const CAPABILITY = "text-generation";

// What this step knows, kept at MODULE level: the wizard mounts a step body
// per navigation and every step is a link in both directions, so Download,
// Next, Back would come back to a step that had forgotten it had started
// anything — the jobs poll not running, the button offering the whole
// download again. `started` is WHEN each model was asked for (see `jobFor`):
// a job row outlives its work, so "is this row mine" is a question about
// time.
let memory: {
  checked: Set<string>;
  started: Map<string, number>;
  errors: Record<string, string>;
} = { checked: new Set(), started: new Map(), errors: {} };

export function forgetModelsStep(): void {
  memory = { checked: new Set(), started: new Map(), errors: {} };
}

/** How long a sent Download reads as busy before its job row has appeared. */
const STARTING_GRACE_MS = 30_000;

/** The job that is telling the truth about `id` right now, or none: a job
 *  still in flight is shown whoever started it; a TERMINAL job only when it
 *  belongs to this visit (asked for here, finished after the ask). */
function jobFor(id: string, jobs: Map<string, Job>, started: Map<string, number>): Job | undefined {
  const job = jobs.get(id);
  if (!job) return undefined;
  if (!isTerminal(job)) return job;
  const askedAt = started.get(id);
  if (askedAt === undefined) return undefined;
  if (job.finished_at == null) return undefined;
  return job.finished_at * 1000 >= askedAt ? job : undefined;
}

/** The offer, once, at the wizard level (the step list does not depend on
 *  it here, but one fetch per wizard is still the right number). */
export function useModelPicks(): ModelPicks {
  const [picks, setPicks] = useState<ModelPicks>(null);
  useEffect(() => {
    let alive = true;
    getOnboardingModels().then(
      ({ models }) => {
        if (alive) setPicks(models);
      },
      () => {
        if (alive) setPicks([]);
      },
    );
    return () => {
      alive = false;
    };
  }, []);
  return picks;
}

/** Jobs, polled for as long as this step is on screen (the wizard has no
 *  download manager on screen, so progress has to be drawn in the step).
 *  `refresh` is the out-of-band tick a click needs. */
function useJobs(): { jobs: Job[]; refresh: () => void } {
  const [jobs, setJobs] = useState<Job[]>([]);
  const lastRunning = useRef(Date.now());
  const refresh = useRef(() => {});
  useEffect(() => {
    let epoch = 0;
    let alive = true;
    let timer: number | undefined;
    const tick = () => {
      const mine = ++epoch;
      fetchJobs().then(
        ({ jobs: next }) => {
          if (!alive || mine !== epoch) return;
          setJobs(next);
          if (next.some(isRunning)) lastRunning.current = Date.now();
          timer = window.setTimeout(tick, pollInterval(next, Date.now() - lastRunning.current));
        },
        () => {
          if (alive && mine === epoch) timer = window.setTimeout(tick, 4000);
        },
      );
    };
    refresh.current = () => {
      if (!alive) return;
      if (timer !== undefined) window.clearTimeout(timer);
      tick();
    };
    tick();
    return () => {
      alive = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);
  return { jobs, refresh: () => refresh.current() };
}

function selectedTotal(picks: OnboardingModelPick[], ids: Iterable<string>): { bytes: number; count: number } {
  const wanted = new Set(ids);
  let bytes = 0;
  let count = 0;
  for (const p of picks) {
    if (!wanted.has(p.id)) continue;
    count += 1;
    if (p.size_gb != null) bytes += p.size_gb * 1e9;
  }
  return { bytes, count };
}

export function ModelsStep({
  picks,
  eyebrow,
  onWork,
}: {
  picks: ModelPicks;
  eyebrow: ReactNode;
  onWork: (busy: boolean) => void;
}) {
  const [checked, setCheckedState] = useState<Set<string>>(memory.checked);
  const [started, setStartedState] = useState<Map<string, number>>(memory.started);
  const [errors, setErrorsState] = useState<Record<string, string>>(memory.errors);
  const setChecked = (next: Set<string>) => {
    memory.checked = next;
    setCheckedState(next);
  };
  const setStarted = (next: Map<string, number>) => {
    memory.started = next;
    setStartedState(next);
  };
  const setErrors = (next: Record<string, string>) => {
    memory.errors = next;
    setErrorsState(next);
  };

  const { jobs, refresh } = useJobs();
  // `job.title` is the repo id (`supervisor.load`'s own `title=model`).
  const jobByModel = useMemo(
    () => new Map(jobs.filter((j) => j.owner === "server").map((j) => [j.title, j])),
    [jobs],
  );

  const rows = (picks ?? []).map((pick) => {
    const job = jobFor(pick.id, jobByModel, started);
    const here = pick.downloaded || job?.state === "done";
    const askedAt = started.get(pick.id);
    const awaiting = askedAt !== undefined && job === undefined && !here && Date.now() - askedAt < STARTING_GRACE_MS;
    const busy = awaiting || pick.downloading || (job !== undefined && !isTerminal(job));
    return { pick, job, busy, here, pending: checked.has(pick.id) && !busy && !here };
  });
  const pending = rows.filter((r) => r.pending);
  const busyCount = rows.filter((r) => r.busy).length;
  useEffect(() => onWork(pending.length > 0), [onWork, pending.length]);
  // THE STAGE: any offered model here = complete; none here but one in
  // flight = partial; nothing = pending. Keyed on the counts.
  const hereCount = rows.filter((r) => r.here).length;
  const rowCount = rows.length;
  useEffect(() => {
    if (picks === null || rowCount === 0) return;
    const status = hereCount > 0 ? "complete" : busyCount > 0 ? "partial" : "pending";
    reportStage("models", status, {
      offered: rows.map((r) => r.pick.id),
      here: rows.filter((r) => r.here).map((r) => r.pick.id),
      downloading: rows.filter((r) => r.busy).map((r) => r.pick.id),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `rows` is a per-render array; the counts are its signal
  }, [picks, rowCount, hereCount, busyCount]);

  const setRow = (id: string, on: boolean) => {
    const next = new Set(checked);
    if (on) next.add(id);
    else next.delete(id);
    setChecked(next);
  };

  const start = () => {
    const wanted = pending.map((r) => r.pick);
    if (wanted.length === 0) return;
    const asked = new Map(started);
    const now = Date.now();
    for (const p of wanted) asked.set(p.id, now);
    setStarted(asked);
    const cleared = { ...errors };
    for (const p of wanted) delete cleared[p.id];
    setErrors(cleared);
    // One request per model, each caught on its own; a model already being
    // fetched JOINS that fetch rather than racing it (supervisor.load).
    for (const p of wanted) {
      downloadAiModel(p.id, CAPABILITY).then(refresh).catch((e: unknown) => {
        const message = e instanceof Error ? e.message : String(e);
        memory.errors = { ...memory.errors, [p.id]: message };
        setErrorsState(memory.errors);
        const remaining = new Map(memory.started);
        remaining.delete(p.id);
        memory.started = remaining;
        setStartedState(remaining);
      });
    }
  };

  const pendingTotal = selectedTotal(picks ?? [], pending.map((r) => r.pick.id));
  const models = `${pending.length} model${pending.length === 1 ? "" : "s"}`;
  const startLabel = pendingTotal.bytes > 0 ? `Download ${models} · ~${formatSize(pendingTotal.bytes)}` : `Download ${models}`;

  return (
    <div className="flex flex-col gap-6">
      <StepHeader
        eyebrow={eyebrow}
        title="Local models, if you want them"
        lead="A bot can run on a model in this Mac's own memory instead of Claude Code — nothing leaves the machine. Optional: pick one here and it downloads in the background while you make your first bot; skip, and a bot set to a local model asks before its first task. Nothing is selected for you."
      />

      {picks === null ? (
        <div className="flex flex-col gap-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-xl" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">No local model can be offered on this Mac.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {rows.map((r) => (
            <ModelRow
              key={r.pick.id}
              pick={r.pick}
              checked={checked.has(r.pick.id)}
              busy={r.busy}
              here={r.here}
              job={r.job}
              error={errors[r.pick.id]}
              onToggle={(on) => setRow(r.pick.id, on)}
            />
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={pending.length > 0 ? "accent" : "outline"}
          className={pending.length > 0 ? "onboarding-accent" : undefined}
          onClick={start}
          disabled={pending.length === 0}
        >
          {pending.length > 0
            ? startLabel
            : busyCount > 0
              ? "Downloading in the background"
              : rows.length > 0 && rows.every((r) => r.here)
                ? "Every model is already here"
                : "Nothing selected"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {busyCount > 0
            ? "Carry on — the download keeps running while you finish setup."
            : "Downloads run in the background. You can go to the next step straight away."}
        </span>
      </div>

      <p className="m-0 text-xs text-muted-foreground">
        A bot uses a local model only when its Settings say so: open the bot's Settings and set Model to Gemma
        4B or 12B. Presets start on a Claude model.
      </p>
    </div>
  );
}

function ModelRow({
  pick,
  checked,
  busy,
  here,
  job,
  error,
  onToggle,
}: {
  pick: OnboardingModelPick;
  checked: boolean;
  busy: boolean;
  here: boolean;
  job: Job | undefined;
  error: string | undefined;
  onToggle: (on: boolean) => void;
}) {
  const fit = fitNote(pick.fit);
  const size = modelSizeLabel(pick.size_gb, job);
  const caption = job ? [jobStatusLine(job), jobAmount(job)].filter(Boolean).join(" · ") : "";
  const fraction = job ? jobFraction(job) : null;
  const failure = error || (job?.state === "error" ? job.message || "The download failed." : null);

  return (
    <li className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        {here ? (
          <span className="mt-0.5 grid size-4 place-items-center rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
            <Check className="size-3" strokeWidth={3} />
          </span>
        ) : busy ? (
          <span className="mt-0.5 grid size-4 place-items-center text-muted-foreground">
            <HardDrive className="size-3.5" />
          </span>
        ) : (
          <Checkbox
            id={`model-${pick.alias}`}
            className="mt-0.5"
            checked={checked}
            onCheckedChange={(next) => onToggle(!!next)}
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm font-medium">
            <label htmlFor={busy || here ? undefined : `model-${pick.alias}`}>{pick.label}</label>
            <span className="text-xs font-normal text-muted-foreground">{pick.id}</span>
          </div>
          {fit && (
            <div className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground" title={fit.title}>
              <span className={`size-1.5 rounded-full ${fit.dot}`} />
              {fit.text}
            </div>
          )}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold tabular-nums">{size}</div>
          <div className="text-xs text-muted-foreground">{here ? "already here" : "download"}</div>
        </div>
      </div>

      {busy && (
        <div className="flex flex-col gap-1.5">
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-500"
              style={{ width: fraction === null ? "15%" : `${Math.round(fraction * 100)}%` }}
            />
          </div>
          <span className="text-xs text-muted-foreground" role="status">
            {caption || "Starting…"}
          </span>
        </div>
      )}

      {failure && (
        <p className="m-0 flex items-start gap-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          {failure}
        </p>
      )}
    </li>
  );
}
