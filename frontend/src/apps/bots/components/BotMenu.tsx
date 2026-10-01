// STUB (scaffold): the bot context menu (OpenBot #cmenu) for the store's ui.menu request, rows from menuIcons.tsx.
// Only the local actions work here (pin, hide, mark as read); the chat agent replaces this file with the full one
// (settings/routines/skills/export/delete via dialogs, clone, live view, window, outside-click/Escape/blur close).
import { useEffect, useLayoutEffect, useRef } from "react";
import { api } from "../lib/api";
import { act, botById, closeMenu, markSeen, useBotsSelector } from "../state/store";
import { menuItems } from "./menuIcons";

export function BotMenu() {
  const menu = useBotsSelector((s) => s.ui.menu);
  const b = useBotsSelector((s) => (menu ? s.bots.find((x) => x.id === menu.id) : undefined));
  const ref = useRef<HTMLDivElement>(null);
  // Clamp into the window once laid out (alignRight: x is the menu's right edge).
  useLayoutEffect(() => {
    const m = ref.current; if (!m || !menu) return;
    const r = m.getBoundingClientRect(), left = menu.alignRight ? menu.x - r.width : menu.x;
    m.style.left = Math.max(8, Math.min(left, innerWidth - r.width - 8)) + "px";
    m.style.top = Math.min(menu.y, innerHeight - r.height - 8) + "px";
  }, [menu]);
  useEffect(() => {
    if (!menu) return;
    const onClick = (e: MouseEvent) => { if (!(e.target as Element).closest?.("#cmenu")) closeMenu(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeMenu(); };
    document.addEventListener("click", onClick); document.addEventListener("keydown", onKey); window.addEventListener("blur", closeMenu);
    return () => { document.removeEventListener("click", onClick); document.removeEventListener("keydown", onKey); window.removeEventListener("blur", closeMenu); };
  }, [menu]);
  return (
    <div id="cmenu" ref={ref} className={`cmenu${menu && b ? " show" : ""}`}>
      {menu && b ? menuItems(b, menu).map((it, i) => it === "hr" ? <hr key={i} /> : (
        <button key={it.a} data-a={it.a} className={it.danger ? "danger" : undefined} onClick={() => {
          closeMenu();
          const t = botById(menu.id); if (!t) return;
          if (it.a === "pin") void act(() => api.flag(t.id, { pinned: !t.pinned }));
          else if (it.a === "hide") void act(() => api.flag(t.id, { hidden: !t.hidden }));
          else if (it.a === "read") markSeen(t.id, t.seq);
        }}>{it.icon}{it.label}</button>
      )) : null}
    </div>
  );
}
