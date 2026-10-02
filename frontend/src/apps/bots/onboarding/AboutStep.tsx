import type { ReactNode } from "react";
// Step 1 — what this is, in one screen. fused-render's About step streamed a
// hero video from its download page; FusedBot's says what a bot is and what
// the next screens check, and that is all a first-run reader needs before
// the machine checks start.
import { Bot, Cpu, Globe, Terminal } from "lucide-react";

import { StepHeader } from "./StepHeader";

const FEATURES = [
  {
    icon: <Globe className="size-4" />,
    title: "Its own Chrome",
    text: "Each bot drives a private browser window: its own logins, cookies and downloads.",
  },
  {
    icon: <Terminal className="size-4" />,
    title: "Claude Code does the thinking",
    text: "A bot runs on Claude Code from this Mac — your subscription, no key to paste.",
  },
  {
    icon: <Cpu className="size-4" />,
    title: "Or a local model",
    text: "Gemma 4B and 12B run on this Mac's own memory, for bots that never leave it.",
  },
  {
    icon: <Bot className="size-4" />,
    title: "Presets to start from",
    text: "Gmail, Sheets, GitHub, Amazon and more — a bot that already knows the site.",
  },
];

export function AboutStep({ eyebrow }: { eyebrow: ReactNode }) {
  return (
    <div className="flex flex-col gap-6">
      <StepHeader
        eyebrow={eyebrow}
        title="Bots that browse for you."
        lead="FusedBot runs browser bots on this Mac: tell one what to do in chat and it opens its own Chrome window and does it. The next two screens check what this Mac has — Claude Code, Chrome, a local model — and the last one makes your first bot. Every step can be skipped."
      />

      <ul className="m-0 grid list-none gap-3 p-0 sm:grid-cols-2">
        {FEATURES.map((f) => (
          <li key={f.title} className="flex items-start gap-3 rounded-xl border border-border bg-card p-4">
            <span className="grid size-8 shrink-0 place-items-center rounded-md bg-muted">{f.icon}</span>
            <div className="min-w-0">
              <div className="text-sm font-medium">{f.title}</div>
              <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{f.text}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
