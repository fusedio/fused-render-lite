// #pmodal (OpenBot dialogs.js pickPreset): "+" asks which preset first (a site the bot knows, with its playbooks, or
// a blank bot), then the bot dialog opens with the pick filled in. Four blank starters fill the first row, then the
// presets in the order the backend returns them. The search box hides the cards that miss; Enter picks the first preset
// still showing (a blank only when nothing else is left). Escape, the backdrop and Cancel dismiss (null).
import { useEffect, useMemo, useRef, useState } from "react";
import { Face } from "../components/Face";
import { api, type Preset } from "../lib/api";
import { filterCards, firstPick, pickCards, type NewBotPick } from "../lib/presets";
import { act } from "../state/store";

export function PresetPicker({ onDone }: { onDone: (pick: NewBotPick | null) => void }) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [query, setQuery] = useState("");
  const qRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let live = true;
    void act(() => api.presets(), true).then((r) => { if (live) setPresets(r?.presets || []); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    qRef.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onDone(null); } };
    document.addEventListener("keydown", key, true);
    return () => document.removeEventListener("keydown", key, true);
  }, []);

  const cards = useMemo(() => pickCards(presets), [presets]);
  const { shown, none } = filterCards(cards, query);
  const visible = new Set(shown);
  return (
    <div id="pmodal" className="modal show" role="dialog" aria-modal="true" aria-label="New bot"
      onClick={(e) => { if (e.target === e.currentTarget) onDone(null); }}>
      <div className="box">
        <h3>New bot</h3>
        <p className="muted">Pick a site, or start blank. Name and rules come next.</p>
        <label className="search psearch">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
          <input id="pq" ref={qRef} type="search" placeholder="Search presets and playbooks" autoComplete="off" value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key !== "Enter") return; e.preventDefault(); const first = firstPick(shown); if (first) onDone(first.pick); }} />
        </label>
        <div id="pgrid" className="pgrid">
          {cards.map((c, i) => {
            const style = visible.has(c) ? undefined : { display: "none" };
            if (c.pick.kind === "blank") {
              const b = c.pick.blank;
              return (
                <button key={`blank:${i}`} className="pcard" data-key="" data-blank={i} data-q={c.q} title="No playbooks; you write the rules" style={style} onClick={() => onDone(c.pick)}>
                  <span className="av"><Face b={{ name: b.name, face: b.face }} /></span><b>{b.name}</b><small>From scratch</small>
                </button>
              );
            }
            const p = c.pick.preset;
            return (
              <button key={p.key} className="pcard" data-key={p.key} data-q={c.q} title={p.skills.join(" · ")} style={style} onClick={() => onDone(c.pick)}>
                <span className="av"><Face b={{ name: p.key, face: { icon: p.key, color: p.color } }} /></span><b>{p.name}</b><small>{p.skills.length} playbooks</small>
              </button>
            );
          })}
        </div>
        <p className="muted" id="pnone" style={{ display: none ? "" : "none" }}>No preset matches. Clear the search and pick a blank bot to write your own rules.</p>
        <div className="row"><button id="pcancel" onClick={() => onDone(null)}>Cancel</button></div>
      </div>
    </div>
  );
}
