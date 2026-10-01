// The bot-level flows behind the dialogs and the bot menu (OpenBot dialogs.js): create, save settings, delete,
// clone, export. Each runs its calls through act() so the page shows the effect and errors land in the banner.
import { api, type Bot, type Face } from "../lib/api";
import { act, cur, getState, select, setState } from "../state/store";
import { askConfirm } from "./ask";

/** What the bot dialog hands back on OK (OpenBot botDialog's read()). */
export interface BotDialogValue {
  name: string; model: string; effort: string; instructions: string; memory: string; approval: string; buildAccess: string;
  encrypt: boolean; profile: string; face: Face; imessage: string; imessageTo: string;
}

/** "+ New bot": create, then the iMessage fields, the Chrome profile and the face; select it and drop focus into the composer. */
export async function createBot(v: BotDialogValue): Promise<void> {
  const r = await act(() => api.create({ name: v.name, model: v.model, effort: v.effort, instructions: v.instructions, approval: v.approval, build_access: v.buildAccess, encrypt: v.encrypt }));
  const id = r?.id;
  if (id && (v.imessage || v.imessageTo)) await act(() => api.settings(id, { name: v.name, imessage_handle: v.imessage, imessage_to: v.imessageTo }));
  if (id && v.profile) await act(() => api.profile(id, v.profile));
  if (id && v.face) await act(() => api.flag(id, { face: v.face }));
  if (id) select(id);
  document.getElementById("input")?.focus();
}

/** Settings → Save: the settings, then the face, then the Chrome profile import. A blank name saves nothing. */
export async function saveSettings(id: string, v: BotDialogValue): Promise<void> {
  if (!v.name) return;
  await act(() => api.settings(id, { name: v.name, model: v.model, effort: v.effort, instructions: v.instructions, memory: v.memory, approval: v.approval,
    build_access: v.buildAccess, encrypt: v.encrypt, imessage_handle: v.imessage, imessage_to: v.imessageTo }));
  if (v.face) await act(() => api.flag(id, { face: v.face }));
  if (v.profile) await act(() => api.profile(id, v.profile));
}

/** Delete… (confirmed): drops the bot's events and cursor so nothing of it lingers, then deletes it. Defaults to the selected bot. */
export async function deleteBot(b: Bot | undefined = cur()): Promise<void> {
  if (!b) return;
  if (!(await askConfirm(`Delete "${b.name}"?`, "Its browser profile and history are removed."))) return;
  const s = getState(), events = { ...s.events }, cursors = { ...s.cursors };
  delete events[b.id]; delete cursors[b.id];
  setState({ events, cursors });
  await act(() => api.remove(b.id));
}

/** Clone: the copy is selected. */
export async function cloneBot(id: string | undefined = getState().sel || undefined): Promise<void> {
  if (!id) return;
  const r = await act(() => api.clone(id));
  if (r?.id) select(r.id);
}

/** Export transcript…: the thread as Markdown, downloaded. Defaults to the selected bot. */
export async function exportBot(b: Bot | undefined = cur()): Promise<void> {
  if (!b) return;
  const r = await act(() => api.exportTranscript(b.id), true);
  if (!r?.text) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([r.text], { type: "text/markdown" })); a.download = r.name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
