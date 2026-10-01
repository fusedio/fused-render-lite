// STUB (scaffold): the modal host for the store's ui.dialog request (newBot, settings, routines, skills, usage, …).
// Renders an empty OpenBot .modal box so a request is visible; the dialogs agent replaces this file
// (BotDialog, FacePicker, Confirm, Routines, Skills, Usage).
import { closeDialog, useBotsSelector } from "../state/store";

export function Dialogs() {
  const req = useBotsSelector((s) => s.ui.dialog);
  return (
    <div className={`modal${req ? " show" : ""}`} onClick={(e) => { if (e.target === e.currentTarget) closeDialog(); }}>
      <div className="box">
        <h3>{req ? req.kind : ""}</h3>
        <div className="row"><button onClick={closeDialog}>Close</button></div>
      </div>
    </div>
  );
}
