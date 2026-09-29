// Render App's entry: fused-render's shell bootstrap (main.tsx) minus the
// explorer's bookmark / recents hydration, mounting `LiteApp` — the Tasks
// page and the Claude chat, and nothing else of the shell. Everything under
// `src/` except this file, `LiteApp.tsx` and `lite.html` is fused-render's
// frontend copied verbatim (scripts/sync_claude_tasks.py --frontend).
import { createRoot } from "react-dom/client";
import { TroubleCard } from "@platform/ui/TroubleCard";
import { IS_EMBED } from "@platform/lib/router";
import { clearListPrefetch, getConfig } from "@platform/lib/api";
import { notifyFsChanged } from "@apps/explorer/listing/fsChangeBus";
import LiteApp from "./LiteApp";
import "./shell.css";

// The chat's app pane writes view params via parent.history.replaceState,
// which fires no event; the router listens for `fused:urlchange` (main.tsx).
const origReplaceState = history.replaceState.bind(history);
history.replaceState = function (...args: Parameters<History["replaceState"]>) {
  origReplaceState(...args);
  window.dispatchEvent(new Event("fused:urlchange"));
};
const origPushState = history.pushState.bind(history);
history.pushState = function (...args: Parameters<History["pushState"]>) {
  origPushState(...args);
  window.dispatchEvent(new Event("fused:urlchange"));
};

declare global {
  interface Window {
    _fusedFsChanged?: () => void;
  }
}

// static/runtime.js reports a filesystem change up the ancestor chain through
// this global (main.tsx); the chat's pane iframe carries that runtime.
window._fusedFsChanged = () => {
  clearListPrefetch();
  notifyFsChanged();
};

if (IS_EMBED) document.body.classList.add("embed");

const root = createRoot(document.getElementById("root")!);

getConfig().then(
  (config) => {
    root.render(<LiteApp config={config} />);
  },
  (err: Error) =>
    root.render(
      <div className="trouble-page">
        <TroubleCard
          what="loading the app's configuration at startup (GET /api/config)"
          error={String(err.message || err)}
          facts={{ page: location.pathname + location.search }}
          onRetry={() => location.reload()}
        />
      </div>
    )
);
