// STUB (scaffold): the middle column's OpenBot markup with its ids/classes and no behaviour beyond the header face,
// the bot name and the preview toggle. The chat agent replaces this file (Thread, Composer, ThreadSearch, ToBottom).
import { toggleRight } from "../lib/layout";
import { cur, useBots } from "../state/store";
import { Face } from "./Face";

export function ChatPane() {
  useBots();
  const b = cur();
  return (
    <section className="chat">
      <header>
        <span className="av sm" id="cav" title="Settings">
          <span id="cavt">{b ? <Face b={b} svgId="hface" /> : null}</span>
          <span className={`dot ${b ? b.status : ""}`} id="cdot" />
        </span>
        <div className="title"><b id="cname">{b ? b.name : "Select a bot"}</b><small id="csub" /></div>
        <div className="searchbar" id="searchbar">
          <input id="searchq" type="search" placeholder="Find in this thread…" />
          <span id="searchn" className="n" />
          <button id="searchx" title="Close (Esc)">×</button>
        </div>
        <button id="searchtog" className="ptog" title="Search this thread (⌘F / Ctrl+F)" disabled={!b}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        </button>
        <button id="rcol" className="ptog" title="Show the browser preview" onClick={toggleRight}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></svg>
        </button>
      </header>
      <div className="tw">
        <div className="thread" id="thread"><div className="empty">Pick a bot on the left, or add one.</div></div>
        <button className="tobottom" id="tobottom" type="button" title="Jump to the latest message" aria-label="Jump to the latest message">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5v14" /><path d="m19 12-7 7-7-7" /></svg>
          <span id="tobottomn" />
        </button>
      </div>
      <div className="composer">
        <div className="ctl">
          <button id="pause" disabled>Pause</button>
          <button id="resume" disabled>Resume</button>
          <button id="stop" className="danger" disabled>Stop</button>
        </div>
        <div className="chips" id="attach" />
        <div className="row" id="crow">
          <div className="line">
            <button id="attachbtn" disabled title="Attach a file for the bot (or paste / drop one here). It lands in this bot's files folder, so it can upload it to a page.">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 7v10M7 12h10" /></svg>
            </button>
            <textarea id="input" rows={1} placeholder="Message…" disabled data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false" />
            <button id="send" className="primary" title="Send (Enter)" disabled>Send</button>
          </div>
        </div>
      </div>
    </section>
  );
}
