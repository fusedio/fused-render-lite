// #astrip (OpenBot apps.js renderStarters / starterAction): the starter apps, one small row above the gallery with an
// Install / Update / Open button each. AppsPanel owns the rows and their load order (after the gallery, then the slow
// status call); this draws them and runs the buttons. Install reloads the apps and goes straight into the new app's
// viewer (its Setup box is the next step); a failure lands in the banner and re-enables the button.
import { useState } from "react";
import { api, starterIconUrl, type AppRow } from "../lib/api";
import { askConfirm } from "../dialogs/ask";
import { errMsg, showBanner } from "../state/store";
import { viewApp } from "./apps";
import { SHIPS_WITH, busyLabel, starterBadge, starterButton, updateConfirm, type StarterKind, type StarterRow } from "./starters";

export interface StartersStripProps {
  rows: StarterRow[];
  /** The gallery's rows, for Open. */
  apps: AppRow[];
  /** Rescan the apps folder (and the starters after it); resolves with the fresh gallery rows. */
  reloadApps: () => Promise<AppRow[] | null>;
}

export function StartersStrip({ rows, apps, reloadApps }: StartersStripProps) {
  const [busy, setBusy] = useState<Record<string, StarterKind>>({});
  if (!rows.length) return <div className="astrip" id="astrip" hidden />;

  const starterAction = async (kind: StarterKind, s: StarterRow) => {
    if (kind === "open") {
      const a = apps.find((x) => x.dir === s.dir);
      if (a) viewApp(a); else showBanner(`${s.name} is installed at ${s.dir} but not listed yet; press Refresh.`);
      return;
    }
    if (kind === "update") { const [t, x] = updateConfirm(s); if (!(await askConfirm(t, x, "Update", false))) return; }
    setBusy((b) => ({ ...b, [s.key]: kind }));
    try {
      const r = await (kind === "update" ? api.starterUpdate(s.key) : api.starterInstall(s.key));
      const fresh = await reloadApps();
      const a = (fresh || []).find((x) => x.dir === r.dir);
      if (kind === "install" && a) viewApp(a);  // straight into the app: its Setup box is the next step
    } catch (e) { showBanner(`Could not ${kind} ${s.name}: ${errMsg(e)}`); }
    finally { setBusy((b) => { const n = { ...b }; delete n[s.key]; return n; }); }
  };

  return (
    <div className="astrip" id="astrip">
      <div className="ahd"><b>Starter apps</b><small>Ready-made apps that ship with {SHIPS_WITH}; a bot made from the matching preset installs its own.</small></div>
      <div className="srow">
        {rows.map((s, i) => {
          const badge = starterBadge(s), btn = starterButton(s), running = busy[s.key];
          return (
            <div key={s.key} className="scard" data-i={i}>
              {s.icon ? <img className="ico" src={starterIconUrl(s.key)} alt="" /> : <span className="ico ph">{s.name.slice(0, 1)}</span>}
              <div className="txt"><b>{s.name}</b><small title={s.desc}>{s.desc}</small></div>
              {s.tools ? <span className="badge" title="Bots can call these tools">⚒ {String(s.tools)}</span> : null}
              {badge ? <span className={badge.cls} title={badge.title}>{badge.text}</span> : null}
              <button data-s={btn.kind} className={btn.primary ? "primary" : undefined} title={btn.title} disabled={!!running}
                onClick={() => { void starterAction(btn.kind, s); }}>{running ? busyLabel(running) : btn.label}</button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
