// #bmodal (OpenBot dialogs.js botDialog): shared by "+ New bot" (create) and Settings (edit). The form is a snapshot
// of the bot as the dialog opened (later polls never reset what you typed). Save stays greyed until something
// differs from that snapshot; Create is always live. The backdrop with unsaved edits asks first; Cancel and Escape
// (in Name) are immediate; Enter in Name is OK.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { Face } from "../components/Face";
import { api, type Bot, type ChromeProfile, type Face as FaceT } from "../lib/api";
import { faceOf } from "../lib/face";
import { imessageStatus } from "../lib/live";
import { act, getState, useBotsSelector } from "../state/store";
import type { BotDialogValue } from "./actions";
import { askConfirm, pickFace } from "./ask";

export const MODELS: [string, string][] = [
  ["haiku", "Haiku · fastest"], ["sonnet", "Sonnet · balanced"], ["opus", "Opus · strongest"], ["fable", "Fable · most capable"],
  ["local-4b", "Gemma 4B · local model"], ["local-9b", "Gemma 12B · local model"],
];
export const EFFORTS: [string, string][] = [["low", "Low · quickest"], ["medium", "Medium"], ["high", "High · careful"], ["xhigh", "Extra high · slowest"]];

export interface BotDialogProps {
  /** The bot being edited; absent for "+ New bot". */
  bot?: Bot;
  onClose: (v: BotDialogValue | null) => void;
}

export function BotDialog({ bot, onClose }: BotDialogProps) {
  const editing = !!bot;
  const [title] = useState(() => (bot ? `Settings · ${bot.name}` : "New bot"));
  // What the dialog opened with (OpenBot's botDialog arguments).
  const [init] = useState(() => bot
    ? { name: bot.name, model: bot.model || "sonnet", effort: bot.effort || "low", instructions: bot.instructions || "", memory: bot.memory || "",
        approval: bot.approval || "ask", buildAccess: bot.build_access || "scoped", encrypt: !!bot.encrypt, imessage: bot.imessage || "", imessageTo: bot.imessage_to || "" }
    : { name: `Bot ${getState().bots.length + 1}`, model: "sonnet", effort: "low", instructions: "", memory: "",
        approval: "ask", buildAccess: "scoped", encrypt: false, imessage: "", imessageTo: "" });
  const [name, setName] = useState(init.name);
  const [model, setModel] = useState(init.model);
  const [effort, setEffort] = useState(init.effort);
  const [instructions, setInstructions] = useState(init.instructions);
  const [memory, setMemory] = useState(init.memory);
  const [approval, setApproval] = useState(init.approval);
  const [buildAccess, setBuildAccess] = useState(init.buildAccess);
  const [encrypt, setEncrypt] = useState(init.encrypt);
  const [imessage, setImessage] = useState(init.imessage);
  const [imessageTo, setImessageTo] = useState(init.imessageTo);
  const [profile, setProfile] = useState("");
  const [profiles, setProfiles] = useState<ChromeProfile[]>([]);
  const [face, setFace] = useState<FaceT | null | undefined>(bot?.face);
  const imsgState = useBotsSelector((s) => s.imessage);

  // The avatar subject: the face is hashed from the id (or, for a new bot, the name it opened with) until one is picked.
  const bm = useMemo(() => ({ id: bot?.id, name: init.name, face }), [bot?.id, init.name, face]);
  const read = (): BotDialogValue => ({ name: name.trim(), model, effort, instructions, memory, approval, buildAccess, encrypt, profile,
    face: faceOf(bm), imessage: imessage.trim(), imessageTo: imessageTo.trim() });  // the face shown is the face kept
  const [initial] = useState(() => JSON.stringify(read()));
  const okDisabled = editing && JSON.stringify(read()) === initial;

  // The title tracks the Name field ("Settings · <name>"); falls back to the given title when Name is blank.
  const [prefix] = title.split(" · ");
  const n = name.trim();
  const shownTitle = n && title.includes(" · ") ? `${prefix} · ${n}` : title;

  // Your Chrome's profiles, listed fresh each time; picking one copies it into the bot on save.
  useEffect(() => {
    let live = true;
    void act(() => api.profiles(), true).then((r) => { if (live) setProfiles(r?.profiles || []); });
    return () => { live = false; };
  }, []);

  // Instructions and Memory grow with their text (up to the CSS max); refit on open (after layout: a hidden textarea reports 0) and as you type.
  const instrRef = useRef<HTMLTextAreaElement>(null), memRef = useRef<HTMLTextAreaElement>(null), nameRef = useRef<HTMLInputElement>(null);
  const fitBmText = () => { for (const t of [instrRef.current, memRef.current]) if (t) { t.style.height = "0"; t.style.height = t.scrollHeight + 2 + "px"; } };
  useLayoutEffect(() => {
    nameRef.current?.focus(); nameRef.current?.select();
    fitBmText(); const raf = requestAnimationFrame(fitBmText);
    return () => cancelAnimationFrame(raf);
  }, []);

  const ok = () => { if (!okDisabled) onClose(read()); };
  const editAvatar = async () => {
    const f = await pickFace(bm, (d) => setFace(d));
    setFace(f);
  };
  // Click on the backdrop dismisses; with unsaved edits it asks first (Discard, or Cancel to keep editing).
  const onBackdrop = async (e: MouseEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (okDisabled || !editing) { onClose(null); return; }
    if (await askConfirm("Discard changes?", "You have unsaved changes to this bot's settings.", "Discard")) onClose(null);
  };

  return (
    <div id="bmodal" className="modal show" role="dialog" aria-modal="true" aria-labelledby="bmtitle" onClick={(e) => { void onBackdrop(e); }}>
      <div className="box">
        <h3 id="bmtitle">{shownTitle}</h3>
        <div className="body">
          <div className="avwrap" id="bmavwrap" title="Edit avatar" onClick={() => { void editAvatar(); }}>
            <span className="av" id="bmface"><Face b={bm} /></span><span className="hint">Edit avatar</span>
          </div>
          <label className="field">Name<input id="bmname" ref={nameRef} placeholder="e.g. LinkedIn scout" value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") ok(); if (e.key === "Escape") onClose(null); }} /></label>
          <div className="pair">
            <label className="field">Model
              <select id="bmmodel" title="Applies from the next task" value={model} onChange={(e) => setModel(e.target.value)}>
                {MODELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="field">Effort
              <select id="bmeffort" title="How much the bot thinks per step; applies from the next task" value={effort} onChange={(e) => setEffort(e.target.value)}>
                {EFFORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </div>
          <label className="field">Instructions
            <textarea id="bminstr" ref={instrRef} rows={2} value={instructions} onChange={(e) => { setInstructions(e.target.value); fitBmText(); }}
              placeholder="Standing rules for every task, e.g. “Browse LinkedIn for me. Answer in bullets. Never like or comment.”" /></label>
          <label className="field" id="bmmemwrap" style={{ display: editing ? "" : "none" }}>Memory <small>· notes the bot keeps between tasks</small>
            <textarea id="bmmem" ref={memRef} rows={3} value={memory} onChange={(e) => { setMemory(e.target.value); fitBmText(); }}
              placeholder="Empty. The bot adds site quirks and your preferences here as it learns." /></label>
          {/* Advanced always starts collapsed; opening it scrolls the dialog body so every field in it is in view, not just the first one. */}
          <details className="adv" id="bmadv" onToggle={(e) => {
            const d = e.currentTarget; if (!d.open) return;
            const body = d.parentElement; requestAnimationFrame(() => body?.scrollTo({ top: body.scrollHeight, behavior: "smooth" }));
          }}>
            <summary>Advanced <small>· approvals, browser profile, iMessage, encryption</small></summary>
            <label className="field">Approvals
              <select id="bmapproval" value={approval} onChange={(e) => setApproval(e.target.value)}>
                <option value="ask">Ask before irreversible actions (send, buy, delete, post)</option>
                <option value="auto">Never ask</option>
              </select>
            </label>
            <label className="field" title="The bot's `build` action hands a spec to Claude Code, which creates a fused-render app under ~/Fused/app. Scoped: Claude asks you before risky tools (the build parks under Builds until you answer). Full access: it runs unattended.">Builds <small>· when this bot asks Claude Code to create an app</small>
              <select id="bmbuild" value={buildAccess} onChange={(e) => setBuildAccess(e.target.value)}>
                <option value="scoped">Scoped · Claude asks before risky steps</option>
                <option value="full">Full access · runs unattended</option>
              </select>
            </label>
            <label className="field" title="Copies that Chrome profile (logins, cookies, extensions, history) into this bot's browser, replacing what it has now. Your own Chrome is not touched.">Browser profile <small>· start from one of your Chrome profiles</small>
              <select id="bmprofile" value={profile} onChange={(e) => setProfile(e.target.value)}>
                <option value="">Keep this bot's own profile{bot?.chrome_profile ? ` (copied from ${bot.chrome_profile})` : ""}</option>
                {profiles.map((p) => <option key={p.dir} value={p.dir}>{p.name}{p.email ? ` · ${p.email}` : ""}</option>)}
              </select>
            </label>
            <label className="field" title="Texts from this number (or Apple ID email) to you on this Mac become tasks for this bot, and its answers and questions are texted back. Needs Messages signed in here and Full Disk Access for FusedRender; see imessage.py.">iMessage <small>· phone number or Apple ID that can text this bot</small>
              <input id="bmimsg" placeholder="+1 555 123 4567 · blank = off" autoComplete="off" value={imessage} onChange={(e) => setImessage(e.target.value)} />
              <small className="stat" id="bmimsgstat">{imessageStatus(init.imessage, imsgState)}</small>
            </label>
            <label className="field" title="The only people the bot's `text` action can iMessage, one per line: a name and a phone number or Apple ID. The number above is always allowed. Every text goes through the approval gate unless Approvals is “Never ask”.">Contacts the bot may text <small>· name + number, one per line</small>
              <textarea id="bmimsgto" rows={2} placeholder={"Ali +1 555 123 4567\nMom mom@icloud.com"} value={imessageTo} onChange={(e) => setImessageTo(e.target.value)} /></label>
            <label className="field check" title="While the browser is closed, the profile is one AES-256 file; the key lives in your macOS Keychain. Lose the Keychain item and saved logins are gone.">
              <input type="checkbox" id="bmencrypt" checked={encrypt} onChange={(e) => setEncrypt(e.target.checked)} /> Encrypt browser profile at rest <small>· key in macOS Keychain</small>
            </label>
          </details>
        </div>
        <div className="row">
          <button id="bmcancel" onClick={() => onClose(null)}>Cancel</button>
          <button id="bmok" className="primary" disabled={okDisabled} onClick={ok}>{editing ? "Save" : "Create"}</button>
        </div>
      </div>
    </div>
  );
}
