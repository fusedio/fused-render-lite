// Render App's shell: the two routes it hosts, drawn by fused-render's own
// components — `/tasks` (shell/Scheduled, the Tasks page with its side peek)
// and `/chat` (apps/claude ChatMount, the native chat beside the app pane).
// The slice of shell/App.tsx those need: theme sync, hints, task-status
// notifications, the notification column, the Mod+K cheat sheet. No sidebar,
// no explorer, no docks — Render App's windows are the app's own.
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { Config } from "@platform/lib/api";
import { useThemeSync } from "@platform/lib/theme";
import { installHints } from "@platform/lib/hints";
import { isMod } from "@platform/lib/platform";
import { isOverlayOpen } from "@platform/lib/ui-overlay";
import NotificationHost from "@platform/ui/NotificationHost";
import ShortcutsOverlay from "@platform/ui/ShortcutsOverlay";
import { ChatMount, sideFrameSrc, contentModeSrc } from "@apps/claude";
import type { ClaudeAsk } from "@apps/claude";
import type { TasksScope } from "@shell/Scheduled";
import { useTaskStatusNotify } from "@shell/useTaskStatusNotify";

const Scheduled = lazy(() => import("@shell/Scheduled"));

// Render App answers `/api/fs/stat` with this one template for every path;
// the server-side path is `fused_render_app/templates/claude/template.html`,
// which the stat entry carries. Only the flag-off iframe (never mounted with
// the native chat on) needs it spelled client-side; "" makes the src relative
// to whatever the stat said, which is what `resolveAgentDir` uses anyway.
const LEGACY_TEMPLATE = "";

function RouteFallback() {
  return <div className="route-fallback" aria-busy="true" />;
}

/** `/tasks?project=<abs app dir>` — the framed Tasks view an app page builds
 *  (`/api/tasks/ui` -> `/tasks?embed=1&project=…`), narrowed to that folder.
 *  Same rule as shell/App.tsx `tasksScopeFromUrl`: read per render, cached on
 *  the value so the scope's identity is stable across re-renders. */
let urlScope: TasksScope | undefined;
function tasksScopeFromUrl(): TasksScope | undefined {
  const raw = new URLSearchParams(location.search).get("project");
  const project = raw ? raw.replace(/\\/g, "/").replace(/(.)\/+$/, "$1") : "";
  if (!project) return undefined;
  if (urlScope?.project !== project) urlScope = { project, ownFrame: true };
  return urlScope;
}

/** The pending "ask Claude" prompt for `/chat?_file=<file>` — stashed in
 *  sessionStorage by static/runtime.js `_fusedAskClaude` in the page that
 *  opened this window (`window.open` copies sessionStorage across), read
 *  ONCE here and handed to the chat as its `initialAsk`. */
function takeStashedAsk(file: string): ClaudeAsk | undefined {
  try {
    const key = "fused:ask:" + file;
    const value = sessionStorage.getItem(key);
    if (value) sessionStorage.removeItem(key);
    return value || undefined;
  } catch {
    return undefined;
  }
}

function useUrl(): string {
  const [, bump] = useState(0);
  useEffect(() => {
    const on = () => bump((n) => n + 1);
    window.addEventListener("fused:urlchange", on);
    window.addEventListener("popstate", on);
    return () => {
      window.removeEventListener("fused:urlchange", on);
      window.removeEventListener("popstate", on);
    };
  }, []);
  return location.pathname + location.search;
}

/** The chat's target, from either address the shell's own links use:
 *  Render App's `/chat?_file=<abs path>`, or fused-render's explorer view
 *  `/explorer/view/<encoded segments>?_side=claude&session_id=…` (what the
 *  task rows, the peek's Open and the chat's Recent list link to —
 *  platform/lib/router `urlForFsPath`). Both open the same chat here. */
function chatTarget(): string {
  const q = new URLSearchParams(location.search);
  const direct = q.get("_file");
  if (direct) return direct;
  const m = /^\/explorer\/(?:view|embed)\/(.*)$/.exec(location.pathname);
  if (!m || !m[1]) return "";
  const segments = m[1].split("/").filter(Boolean).map((s) => decodeURIComponent(s));
  // Windows drive paths (`C:/…`) keep no leading slash; POSIX paths do.
  return /^[A-Za-z]:$/.test(segments[0]) ? segments.join("/") : "/" + segments.join("/");
}

function ChatRoute() {
  const q = new URLSearchParams(location.search);
  const file = chatTarget();
  const chatOnly = q.get("chat_only") === "1";
  // Read once per mount (`useState` initializer): the ask is consumed.
  const [initialAsk] = useState(() => (file ? takeStashedAsk(file) : undefined));
  if (!file) {
    return <div className="route-fallback">No folder to chat about (`?_file=`).</div>;
  }
  return (
    <ChatMount
      key={file}
      file={file}
      chatOnly={chatOnly}
      paramsSource="url"
      // The flag-off iframe URL: the chat template ships at a fixed place in
      // Render App (server.py TEMPLATES_DIR), so the legacy src is spelled
      // with it; the native chat never loads it.
      legacySrc={chatOnly ? sideFrameSrc(LEGACY_TEMPLATE, file, "", "") : contentModeSrc(LEGACY_TEMPLATE, file, "", "")}
      mountClassName="preview-frame is-shown"
      title="Claude"
      initialAsk={initialAsk}
      recap
    />
  );
}

export default function LiteApp({ config }: { config: Config }) {
  void config;
  useThemeSync();
  useEffect(() => {
    installHints();
  }, []);
  useTaskStatusNotify();

  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const shortcutsOpenRef = useRef(false);
  shortcutsOpenRef.current = shortcutsOpen;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing) return;
      if (!isMod(e) || e.key.toLowerCase() !== "k") return;
      if (shortcutsOpenRef.current) return;
      if (isOverlayOpen()) return;
      e.preventDefault();
      setShortcutsOpen(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const url = useUrl();
  const pathname = location.pathname;
  let main;
  if (pathname === "/chat" || pathname.startsWith("/explorer/")) {
    main = (
      <div id="content" className="lite-chat">
        <ChatRoute key={chatTarget()} />
      </div>
    );
  } else {
    // `/tasks`, and anything else: the Tasks page.
    main = (
      <div id="content">
        <Suspense fallback={<RouteFallback />}>
          <Scheduled scope={tasksScopeFromUrl()} />
        </Suspense>
      </div>
    );
  }
  void url;
  return (
    <div id="app" className="lite">
      {main}
      <NotificationHost />
      {shortcutsOpen && <ShortcutsOverlay onClose={() => setShortcutsOpen(false)} />}
    </div>
  );
}
