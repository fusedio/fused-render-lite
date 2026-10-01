// Render App's self-update banner (what the old static/index.html launcher showed), as one line under #banner.
// Polls GET /api/update on lib/update.ts's cadence: 2 s for the first 20 s (the server's first check lands ~1 s
// after boot), 2 s while a check or install runs, 60 s otherwise; stops for the session when the server has no
// updater (a null that outlives the hot window, or a 404). What each state says is describeUpdate()'s; this
// component owns the timer and the clicks. Renders nothing at all when there is nothing to say.
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { describeUpdate, pollDelay, POLL_IDLE_MS, updateApi, type UpdateAction, type UpdateStatus } from "../lib/update";

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function UpdateBanner() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);
  // Re-arms the poll at the cadence a status calls for; set by the effect, used by the click handlers too.
  const rearm = useRef<(s: UpdateStatus | null) => void>(() => {});

  useEffect(() => {
    const startedAt = Date.now();
    let disposed = false, timer: ReturnType<typeof setTimeout> | undefined, gen = 0, last: UpdateStatus | null = null;

    const arm = (s: UpdateStatus | null, fallback?: number) => {
      clearTimeout(timer);
      const mine = ++gen;
      const ms = pollDelay(s, Date.now() - startedAt) ?? fallback;
      if (ms == null || disposed) return;
      timer = setTimeout(() => { if (mine === gen) void tick(); }, ms);
    };
    const tick = async () => {
      const mine = gen;
      try {
        const { update } = await updateApi.status();
        if (disposed || mine !== gen) return;
        last = update ?? null;
        setStatus(last);
        arm(last);
      } catch (e) {
        if (disposed || mine !== gen) return;
        // No updater route on this server: nothing to show, ever.
        if (e instanceof ApiError && e.status === 404) return;
        // Server unreachable: the store's banner tells that story; keep the last status and look again later.
        arm(last, POLL_IDLE_MS);
      }
    };
    rearm.current = (s) => { last = s; arm(s); };
    void tick();
    return () => { disposed = true; clearTimeout(timer); rearm.current = () => {}; };
  }, []);

  // A fresh state is a fresh story: an old click's failure does not outlive it.
  const shown = status?.state;
  useEffect(() => { setActionErr(null); }, [shown]);

  const view = describeUpdate(status);
  if (!view || !status) return null;

  const run = async (action: UpdateAction) => {
    setBusy(true);
    setActionErr(null);
    try {
      if (action.kind === "relaunch") {
        // The reply goes out first, then the app quits; the window goes with it.
        await updateApi.relaunch();
        return;
      }
      const next = action.kind === "cancel" ? await updateApi.cancel() : await updateApi.install(status.latest_version);
      setStatus(next);
      rearm.current(next);
    } catch (e) {
      setActionErr(errMsg(e));
    } finally {
      if (action.kind !== "relaunch") setBusy(false);
      else setTimeout(() => setBusy(false), 10_000); // still here after 10 s: the quit did not happen
    }
  };

  const err = actionErr != null || view.tone === "err";
  const fraction = view.progress?.fraction;
  return (
    <div id="update" className={err ? "err" : ""} role="status" aria-live="polite">
      <span>
        {actionErr ?? view.text}
        {!actionErr && view.sub ? <em> · {view.sub}</em> : null}
      </span>
      {view.action ? (
        <button
          className={(view.action.kind === "cancel" ? "" : "primary") + (busy ? " busy" : "")}
          disabled={busy}
          onClick={() => void run(view.action!)}
        >{view.action.label}</button>
      ) : null}
      {view.progress ? (
        <div className={"ubar" + (fraction == null ? " indet" : "")}>
          <i style={fraction == null ? undefined : { width: `${(fraction * 100).toFixed(1)}%` }} />
        </div>
      ) : null}
    </div>
  );
}
