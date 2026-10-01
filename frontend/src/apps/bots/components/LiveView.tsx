// STUB (scaffold): OpenBot's #full live view, shown while the store's `fast` flag is on. The preview/live-view agent
// replaces this file (CDP screencast, take over / hand back, tab strip, nav bar, input forwarding).
import { setFast, useBotsSelector } from "../state/store";
import { Toast } from "./Toast";

export function LiveView() {
  const open = useBotsSelector((s) => s.fast);
  return (
    <div id="full" className={open ? "show" : ""}>
      <div className="topbar">
        <button id="giveback2" className="backtxt" title="Back to chat; the bot continues" onClick={() => setFast(false)}>Back</button>
        <div className="tabstrip" id="tabstrip" />
      </div>
      <header><span className="dot" id="fdot" /><span className="fstat" id="fstat" title="What the bot is doing" /></header>
      <div className="stage" id="stage" tabIndex={0}><Toast id="ftoast" /></div>
    </div>
  );
}
