// The viewer's ⋯ menu (OpenBot apps.js openAppMenu, which reused #cmenu): everything that used to be a toolbar button,
// plus "New task…", a Claude session inside the app's folder that edits it in place (newAppTask). Its own `.cmenu`
// element (#amenu) so the bot menu's outside-click rule (closest("#cmenu")) stays BotMenu's. Positioned under the
// kebab, right-aligned, clamped into the window; closes on any outside click, Escape or window blur.
// "Pin to menu bar" puts the app in the FusedBot menu-bar item's Pinned section (bots/dock.py): the menu reads
// GET /api/dock as it opens to know which way the item points, and the click POSTs /api/dock/pin.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { api, request } from "../lib/api";
import { errMsg, showBanner } from "../state/store";
import { buildsOn, newAppTask } from "../builds/builds";
import { appEmbedBase, appOpenUrl, appStateParams, closeApps, copyAppState, getViewedApp, viewFrameSrc } from "./apps";
import { showAppBeside } from "./side";

const mi = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
const AMI = {
  reload: mi(<><path d="M20 12a8 8 0 1 1-2.34-5.66" /><path d="M20 4v5h-5" /></>),
  copy: mi(<><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  side: mi(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M14 4v16" /></>),
  tab: mi(<><path d="M14 4h6v6" /><path d="M20 4 10 14" /><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" /></>),
  task: mi(<path d="M12 5v14M5 12h14" />),
  finder: mi(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />),
  pin: mi(<><path d="M12 17v5" /><path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z" /></>),
};
type Act = keyof typeof AMI;

interface DockRow { kind: "bot" | "app"; id?: string; dir?: string }
interface DockEntries { pinned: DockRow[]; recent_bots: DockRow[]; recent_apps: DockRow[] }
const dockApi = {
  entries: () => request<DockEntries>("GET", "/api/dock", undefined, "dock"),
  pin: (dir: string, pinned: boolean) =>
    request<{ ok: true; pinned_apps: string[] }>("POST", "/api/dock/pin", { dir, pinned }, "dock/pin"),
};

function togglePin(pinned: boolean) {
  const app = getViewedApp(); if (!app) return;
  dockApi.pin(app.dir, !pinned).catch((e) => showBanner("Could not change the menu bar: " + errMsg(e)));
}

function run(a: Act) {
  const app = getViewedApp(); if (!app) return;
  const params = appStateParams();
  switch (a) {
    case "reload": viewFrameSrc(appEmbedBase(app.dir, params)); break;  // reload keeps the state, like a browser does
    case "copy": copyAppState(app); break;
    case "side": closeApps(); showAppBeside({ ...app, params }); break;
    case "tab": window.open(appOpenUrl(app.dir, params), "_blank", "noopener"); break;
    case "finder": api.revealApp(app.dir).catch((e) => showBanner("Could not open Finder: " + errMsg(e))); break;
    case "task": void newAppTask(app); break;
  }
}

/** `anchor` is the kebab's rect while the menu is open, else null. */
export function AppMenu({ anchor, onClose }: { anchor: DOMRect | null; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  // null until GET /api/dock answers (the item waits rather than guess which way it points).
  const [pinned, setPinned] = useState<boolean | null>(null);
  useEffect(() => {
    if (!anchor) { setPinned(null); return; }
    const dir = getViewedApp()?.dir; if (!dir) return;
    let live = true;
    dockApi.entries()
      .then((e) => { if (live) setPinned(e.pinned.some((r) => r.kind === "app" && r.dir === dir)); })
      .catch(() => { if (live) setPinned(false); });
    return () => { live = false; };
  }, [anchor]);
  useLayoutEffect(() => {
    const m = ref.current; if (!m || !anchor) return;
    const r = m.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(anchor.right - r.width, innerWidth - r.width - 8)) + "px";
    m.style.top = Math.min(anchor.bottom + 6, innerHeight - r.height - 8) + "px";
  }, [anchor, pinned]);  // the pin item lands a moment after the menu opens: re-clamp
  useEffect(() => {
    if (!anchor) return;
    const onClick = (e: MouseEvent) => { if (!(e.target as Element).closest?.("#amenu")) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("click", onClick); document.addEventListener("keydown", onKey); window.addEventListener("blur", onClose);
    return () => { document.removeEventListener("click", onClick); document.removeEventListener("keydown", onKey); window.removeEventListener("blur", onClose); };
  }, [anchor, onClose]);
  const item = (a: Act, label: string) => <button data-a={a} onClick={() => { onClose(); run(a); }}>{AMI[a]}{label}</button>;
  return (
    <div id="amenu" ref={ref} className={`cmenu${anchor ? " show" : ""}`}>
      {anchor ? (<>
        {item("reload", "Reload")}
        {item("copy", "Copy state")}
        <hr />
        {item("side", "Beside chat")}
        {item("tab", "Open in tab")}
        {item("finder", "Open in Finder")}
        {pinned !== null ? (
          <button data-a="pin" onClick={() => { onClose(); togglePin(pinned); }}>
            {AMI.pin}{pinned ? "Unpin from menu bar" : "Pin to menu bar"}
          </button>
        ) : null}
        {buildsOn() ? <><hr />{item("task", "New task…")}</> : null}
      </>) : null}
    </div>
  );
}
