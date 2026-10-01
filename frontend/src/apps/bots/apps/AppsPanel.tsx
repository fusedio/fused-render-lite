// STUB (scaffold): OpenBot's #apanel (Apps gallery), shown while ui.panel === "apps", and the empty #vpanel viewer.
// The builds/apps agent replaces this file (gallery, viewer, kebab menu, upload/drop, side app, app cards).
import { closePanel, useBotsSelector } from "../state/store";

export function AppsPanel() {
  const open = useBotsSelector((s) => s.ui.panel === "apps");
  return (
    <>
      <div id="apanel" className={open ? "show" : ""}>
        <div className="topbar">
          <button id="aback" className="backtxt" title="Back to bots (Esc)" onClick={closePanel}>Back</button>
          <b className="ttl">Apps</b><small className="muted">Fused apps the builds have made</small>
          <span className="winacts"><button id="areload" title="Rescan the apps folder">Refresh</button><button id="anew" className="primary">New build</button></span>
        </div>
        <div className="agrid" id="agrid"><div className="empty">Looking for apps…</div></div>
      </div>
      <div id="vpanel" />
    </>
  );
}
