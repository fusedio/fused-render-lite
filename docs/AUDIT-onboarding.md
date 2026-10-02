# Onboarding audit: what to bring from fused-render to FusedBot, and why

Date: 2026-10-02. Source: `fused-render` main (`fused_render/shell/onboarding.py`,
`frontend/src/shell/onboarding/`, `fused_render/claude_health.py`,
`claude_install.py`, `claude_login.py`, `server/routers/claude_health.py`).
Target: this repo (FusedBot, `fused_render_app/`, `frontend/src/apps/bots/`).

This is an audit, not a plan of record. It says what exists upstream, what
FusedBot does today in the same situation, and a bring / adapt / drop verdict
per piece with the FusedBot-specific reason. Nothing here is built yet.

## 1. What a fresh install hits today (the "why")

A new user installs the DMG, opens FusedBot, and lands on the bots page at `/`
with "No bots yet. Add one and give it a task." Nothing checks the machine
first. The three things every bot needs are discovered one failure at a time,
after the user has already typed a task:

| Need | Where it fails today | How the user learns |
| --- | --- | --- |
| `claude` CLI, signed in | `bots/bot.py::_engine_for` sees no CLI and silently switches to the steps engine, but the model stays what the preset set (`sonnet`): `_resolve_model` only maps `local-*` aliases, never a Claude alias. So the first step calls `fused_ai.text`, which talks to this server's AI relay, which raises `claude binary not found on PATH; install Claude Code or …` (`routes/ai_relay.py:426`). Signed-out CLI: the error comes back from the `claude -p` child. The greeting generated at bot creation (`bot.py:630-670`) fails the same way, but that failure is swallowed and a canned greeting is emitted, so creation looks fine. | An error event in the bot's chat, after the first task. The React shell has a `TroubleCard` (`notfound` / `login` / `limit`) but it is a post-failure card, and the bots page does not render it. |
| Chrome | `bots/browser.py:81` raises `No Chrome/Chromium found in /Applications` when the bot starts its browser. | Error event at first task. |
| A local model (Gemma 4B / 12B, `bots/bot.py::LOCAL_MODELS`) | Only if the user picks `local-4b` / `local-9b`, or has no `claude`. `_ensure_model_ready` asks "Download it now? (~5.2 GB)" as a chat question on the first task and blocks the task on the fetch. | A question in chat, then a wait of minutes on the first task. |

`claude_health.py` here is 40 lines: `resolve()` returns a path or `(None, None)`.
It knows nothing about version, sign-in, account, or brokenness. There is no
`/api/claude/*` route at all. `GET /api/config` says in its own docstring:
"no onboarding".

This is the same shape upstream's `claude_health.py` docstring names as the
problem it was written to fix: "the user did something, it failed, and a card
explained the failure afterwards." The upstream wizard moved those facts in
front of the first prompt. FusedBot is back on the old side of that line, with
one more dependency (Chrome) that upstream never had.

## 2. What upstream has (one paragraph per piece)

**Server flag module** (`shell/onboarding.py`, ~350 lines). Four fields in
`prefs.json`: `completed_at`, `dismissed_at`, `opened_at`, `stages`. Auto-show
only while all three timestamps are null. `stages` is one record per step
(`pending | partial | complete | n/a`, free-form `meta`, `updated_at`) and is
overruled on every read by what the server can see cheaply (`_observe`): FDA
probe, local apps folder, Claude health disk cache, hub cache. Server-side on
purpose: every port is a new browser origin, so localStorage would replay the
wizard. `seed_for_existing_users` marks an upgrade install completed if it
already has apps. Five routes under `/api/onboarding`, all `X-Fused` guarded.

**Wizard route** (`OnboardingWizard.tsx`, 418 lines). `/onboarding`, rendered
alone with no sidebar. Five steps, every one skippable, step id in the query
string, reopen lands on the first step still to do. One yellow button per
screen. Escape dismisses, Cmd+Enter advances. Browser Back or any other exit
counts as dismiss; a navigation off the last step counts as complete.

**About** (71 lines). Title, lead, hero video streamed from `render.fused.io`,
four feature tiles. Viewing it completes it.

**Claude Code step** (239 lines) plus `lib/claude-setup.ts` (the setup
machine) and `ui/ClaudeHealthStrip` (the `IssueRow` with buttons). Four rows:
installed, version at or above `MIN_VERSION`, signed in (names the account),
on shell PATH (optional). Each open row has the strip's button: Install
(`claude_install.py`, native installer, polled), Update, Doctor, Sign in
(`claude_login.py`, runs `claude auth login`; the CLI opens the browser and
binds its own loopback callback, so the app never handles an OAuth code), Add
to PATH. Never blocks Next. Backend is `claude_health.py` (~1200 lines:
resolve through env, PATH, known locations, and a login-shell probe; version
probe; `claude auth status` for sign-in and account; doctor; disk cache keyed
on a binary fingerprint) and seven routes.

**Disk Access step** (190 lines). macOS Full Disk Access: explain, open the
exact Settings pane, detect `pending_relaunch`, offer relaunch.

**Models step** (580 lines) plus `modelPicks.ts`. One `recommended` model per
catalog capability that an engine here can run, video and decisions excluded
by rule. Fit verdict from `fit.py` decides which start checked. Download is a
fire-and-forget supervisor job that outlives the wizard; progress is drawn
in-step from the jobs poll. Nothing blocks.

**First app step** (159 lines). The Home composer or a showcase card. The
only step whose action writes anything. Completing it is completing the
wizard.

**Progress meter** (`progress.ts`, `SetupProgress.tsx`). A `useSyncExternalStore`
store merged from every server reply (newest `updated_at` wins per stage), the
sidebar's "Setup 60%" row, and the collapsed-rail ring. Visible only after the
first stage write and until 100%.

## 3. Verdict per piece

| Upstream piece | Verdict | FusedBot reason |
| --- | --- | --- |
| Server flag module | **Bring, adapted** | Same port-drift argument holds (the server walks ports, `write_server_json`). `shell/prefs.py` here is a stub with no file behind it, so the state goes in its own JSON under `storage.home_dir()` (`~/.fused-render-app/onboarding.json`). Keep all four fields. Replace the three `_observe` probes with FusedBot's: Claude health cache, Chrome probe, hub cache for `LOCAL_MODELS`, bots data root for "first bot". |
| `seed_for_existing_users` | **Bring, must not skip** | 0.11.x is shipped. An upgrade into the build that adds the wizard must never greet a returning user with it. Evidence of a returning user here is any bot under the bots data root (`bots/paths.py`), not an apps folder. |
| Wizard route and shell | **Bring, adapted** | The frame, step list, query-string step id, exit semantics and keyboard rules port as-is. Mount point changes (section 4). Drop the platform `n/a` logic for FDA: this build is macOS-only (non-macOS paths were dropped in 0.8.15). |
| About step | **Shrink** | One screen, no hero video, no file-explorer or Python pitch. Copy says what a bot is and that the next screens check the machine. Could fold into the first check screen as its lead paragraph. |
| Claude Code step | **Bring; this is the largest gap** | The step's four rows and buttons are exactly what section 1 says is missing. Backend work is the bulk: extend lite's `claude_health.py` (it is already the one resolve point the server exports to the relay at startup) with version, `auth status`, account, doctor, fingerprint cache; port `claude_install.py` and `claude_login.py`; add the seven routes. Login is portable without a terminal: upstream's `claude_login.py` spawns `claude auth login` and the CLI opens the browser itself. `useTerminalDockOpen` in `claude-setup.ts` is only a re-check trigger (when the terminal dock closes, health is re-fetched, on the guess the user fixed something there); it gates nothing. FusedBot has no dock, so that trigger is dropped and the window-focus re-check remains. |
| Chrome probe (new) | **Add; upstream has no equivalent** | Every bot needs Chrome; upstream never did. Reuse `browser.py`'s candidate list as a server probe (`/api/bots/chrome` or a field on `/api/config`) and draw it as one row beside the Claude rows: found at path, or "Install Google Chrome" with a link. Also a candidate for the `_observe` overrule. |
| Disk Access step | **Drop** (user's call) | No indexing here. One flag: `bots/imessage.py` reads `~/Library/Messages/chat.db`, which is FDA-gated, and raises "no Full Disk Access to Messages" when it is not. FDA is conditional on the iMessage bridge, not dead. The Settings dialog already shows the bridge status, so the ask can stay where the feature is turned on rather than in the wizard. |
| Models step | **Adapt, do not port `modelPicks.ts`** | The catalog's `recommended` text-generation row is `mlx-community/Qwen3.5-4B-OptiQ-4bit`, which no bot uses. The bots run `LOCAL_MODELS` (Gemma 4 E4B 4-bit, 5.2 GB; Gemma 4 12B QAT 4-bit, 11 GB). The step must key off `LOCAL_MODELS` and run those ids through `fit.py` for the fit note and the preselect rule. Two reasons the step earns its place here. First, a user who picks `local-4b` or `local-9b` today meets the 5 GB "Download now?" question as a chat prompt on their first task and waits on it. Second, nothing today steers a no-`claude` user toward a local model: the engine falls back, the model does not (section 1), so every preset bot fails. The wizard is the one place that can say "no Claude Code found, a local model will run your bots" and start the fetch while the user reads on. Keep nothing-blocks and the supervisor download job; the in-step progress rendering ports as-is (`jobs.py`, `supervisor.py`, `hub_cache.py` are already here). |
| First app step | **Replace with "First bot"** | FusedBot already has the empty-state hero, the preset grid and the `newBot` dialog. The last step is the preset grid. Complete = a bot exists; the server observes the bots data root. The composer and showcase code do not apply. |
| Progress meter | **Mostly drop** | No sidebar. Keep `stages` and `firstOpenStage` so a reopen lands on the first open step. The reopen entry goes in the bot menu or the title bar's utility menu. The `progress.ts` store merge rules are worth keeping even without a meter, because two stage POSTs still race. |
| Hero video | **Drop** | Streams from `render.fused.io`; wrong product, wrong footage. |

## 4. Mount point and shape

Two React entries exist: `bots.html` (`bots.tsx` to `apps/bots/App.tsx`) at
`/`, and `lite.html` (`LiteApp.tsx`) at `/tasks` and `/chat`. The wizard
belongs to the bots entry: the bots page is the front door, and
`platform/lib/api.ts` is copied verbatim from upstream so `OnboardingState`,
`getOnboarding`, `completeOnboarding`, `dismissOnboarding`, `openedOnboarding`
and `setOnboardingStage` are already declared and unused.

Two ways to show it, pick one:

- The server redirects `/` to `/onboarding` while `shouldAutoShow` holds, and
  `/onboarding` serves `bots.html` which routes on `pathname`. Matches upstream
  (a route, not an overlay) and survives a refresh.
- `apps/bots/App.tsx` reads `/api/config` at boot and renders the wizard in
  place of the grid. Fewer moving parts, but the bots store starts polling
  under the wizard.

The first matches upstream and keeps the bots store out of the wizard.

Proposed steps for FusedBot:

1. **Welcome** (About, one screen).
2. **This Mac**: Claude Code rows (installed, version, signed in, PATH
   optional) and one Chrome row. Buttons per open row.
3. **Local models**: Gemma 4B and 12B with fit notes, preselect what fits,
   Download in the background.
4. **First bot**: the preset grid. Complete on first bot.

Four stages count toward "first open step": `claude`, `chrome`, `models`,
`bot`. `about` completes on view as upstream.

## 5. Cost, roughly

| Piece | Backend | Frontend |
| --- | --- | --- |
| Flag module and routes | port, ~250 lines after adapting `_observe` | none |
| Claude health, install, login, doctor | the bulk: `claude_health.py` 1289, `claude_install.py` 372, `claude_login.py` 436, routes 208 lines upstream, trimmed of Windows and Linux paths | `claude-setup.ts` (342) and `ClaudeHealthStrip.tsx` (292, for `IssueRow`) copy near-verbatim |
| Chrome probe | ~40 lines | one row |
| Models step | none new (`fit.py`, `hub_cache.py`, `supervisor.py` present) | `ModelsStep.tsx` with `modelPicks.ts` rewritten against `LOCAL_MODELS` |
| Wizard frame | redirect in `server.py` | `OnboardingWizard.tsx` trimmed |
| First bot | observe bots root | reuse preset grid |

The frontend is the cheaper half. The expensive and valuable half is the
Claude backend, and it pays off outside the wizard too: once
`/api/claude/health` exists, the bots page can draw the `TroubleCard` before a
task is typed, and the engine fallback in `_engine_for` can say why it fell
back.

## 6. Flags

- **Upgrade edge.** Seed completion from the bots data root before the first
  `/api/config` read, or every existing 0.11 user sees the wizard once.
- **No terminal.** Doctor output and the "run this in a terminal" fallbacks
  become copy buttons; the install and login flows do not need a terminal.
- **Local model choice vs catalog.** Keep the wizard's offer and the bot's
  `LOCAL_MODELS` the same table, or they drift the way the catalog already has.
- **Engine fallback without model fallback.** `_engine_for` switches to the
  steps engine when `claude` is missing but leaves a Claude alias as the
  model, which cannot work. Whether or not the wizard lands, that fallback
  should either remap to `local-4b` or say why it cannot run. The wizard
  makes the gap visible; it does not close it.
- **FDA is not gone.** The iMessage bridge needs it; ask there, not at first
  run.
- **Chrome is a hard dependency.** Unlike every upstream step, a missing
  Chrome means no bot works at all. The row should say so without blocking
  Next, since the user may install it and come back.
