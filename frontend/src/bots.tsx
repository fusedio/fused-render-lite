// Browser Bots entry (bots.html → here → apps/bots/App.tsx). Styles: apps/bots/styles/bots.css (Tailwind base +
// OpenBot's app.css), never shell.css.
import { createRoot } from "react-dom/client";
import App from "./apps/bots/App";
import "./apps/bots/styles/bots.css";

// Embedded apps (side app, inline cards, the viewer) write their params to this page's URL through
// parent.history.replaceState (static/runtime.js), which fires no event; the store follows `?bot=` on
// `fused:urlchange`, as the lite entry does for its router.
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

createRoot(document.getElementById("root")!).render(<App />);
