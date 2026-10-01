// STUB (scaffold): the right column's OpenBot markup (header, side-app strip, shot, toast, caption, info) with its
// ids/classes and no behaviour beyond hiding the preview. The preview/live-view agent replaces this file.
import { closePreview } from "../lib/layout";
import { Toast } from "./Toast";

export function PreviewPane() {
  return (
    <section className="preview">
      <header>
        <button id="pclose" className="ptog" title="Hide the browser preview" onClick={closePreview}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
        </button>
        <span style={{ flex: 1 }} />
        <button id="pmore" className="ptog" title="Open live view, settings, routines, skills, clone, delete" disabled>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
        </button>
      </header>
      <div className="sabar" id="sabar" />
      <div className="shotwrap" id="shotwrap" title="Click to watch this bot's browser">
        <div className="ph" id="ph">No browser yet</div>
      </div>
      <Toast id="toast" />
      <div className="cap"><span id="pcap" /><span className="url" id="purl">—</span></div>
      <div className="info" id="info" />
    </section>
  );
}
