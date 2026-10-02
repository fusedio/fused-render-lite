// Browser Bots entry (bots.html → here → apps/bots/App.tsx). Styles: apps/bots/styles/bots.css (Tailwind base +
// OpenBot's app.css), never shell.css.
import { createRoot } from "react-dom/client";
import { getOnboarding } from "@platform/lib/api";
import App from "./apps/bots/App";
import { OnboardingWizard } from "./apps/bots/onboarding/OnboardingWizard";
import { setProgress } from "./apps/bots/onboarding/progress";
import { ONBOARDING_PATH } from "./apps/bots/onboarding/state";
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

// `/onboarding` is the first-run setup wizard (apps/bots/onboarding), rendered
// alone: no bot list, no chat, no store poll. The server sends a never-seen
// install's `/` here (fused_render_app/onboarding.py) and the wizard leaves by
// a plain navigation back to `/`. Its progress snapshot is fetched before the
// first paint so the step it opens on is the first one still to do, not the
// top; a failed fetch opens at the top.
const root = createRoot(document.getElementById("root")!);
if (location.pathname === ONBOARDING_PATH) {
  getOnboarding().then(
    (s) => {
      setProgress(s);
      root.render(<OnboardingWizard />);
    },
    () => root.render(<OnboardingWizard />),
  );
} else {
  root.render(<App />);
}
