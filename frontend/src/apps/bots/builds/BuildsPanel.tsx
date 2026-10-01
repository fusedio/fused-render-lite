// STUB (scaffold): OpenBot's #bpanel (Builds), shown while ui.panel === "builds". The builds agent replaces this
// file (the /tasks iframe, the row filter, the chip numbers via setBuildsChip, the New build dialog).
import { closePanel, useBotsSelector } from "../state/store";

export function BuildsPanel() {
  const open = useBotsSelector((s) => s.ui.panel === "builds");
  return (
    <div id="bpanel" className={open ? "show" : ""}>
      <div className="topbar">
        <button id="bback" className="backtxt" title="Back to bots (Esc); builds keep running" onClick={closePanel}>Back</button>
        <b className="ttl">Builds</b><small className="muted">Claude tasks that create fused apps</small>
        <span className="winacts"><button id="bnew" className="primary">New build</button></span>
      </div>
    </div>
  );
}
