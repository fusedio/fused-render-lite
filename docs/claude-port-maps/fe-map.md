# fused-render frontend: Claude chat + tasks UI — migration map for fused-render-lite

Source repo: /Users/vasu/Documents/fused-dev/fused-render (read-only survey, 2026-09-29).
All frontend paths below are relative to `frontend/src/`.

## 0. Headline facts

- **No stream-json in the browser.** `fused_render/templates/claude/agent.py::_segments_from_rows` reads the CLI's `out.jsonl` (assistant / tool_use / tool_result / result / rate_limit_event / message_start rows) and returns `segments[]`. The client polls `POST /api/run` (agent.py action `poll`) every 400 ms, flat, no backoff, no cursor; each poll replays the whole current turn and the client dedupes.
- **No SSE / WebSocket** anywhere in chat or tasks. Real-time = 400 ms chat poll + `GET /api/tasks/changes?since=<gen>&wait=25` long-poll + `GET /api/tasks/pulse` every 10 s (running) / 30 s (idle).
- **A vanilla original of the chat exists**: `fused_render/templates/claude/template.html` (19,378 lines, ~1 MB, 338 KB gz; CSS lines 1–3888, one `<script>` from 4443). Uses only `fused.runPython / params / rawUrl / ai / uploadFile / capture / autoReload(false)` — **lite's `fused_render_app/static/runtime.js` already implements every one**. The only `fused.io` hits are `render.fused.io` help links.
- **Task pages have NO vanilla original** (Scheduled / ScheduleTaskViews / ScheduleCalendar / NewJobModal / TaskPeek / tasks-lib ≈ 30.2k src + 10k CSS).
- **`.claude-design/`** (design.md + inventory/*.md, cited by apps/claude/README.md with T:<line> cites) is **absent from the repo**.
- **ARCHITECTURE.md / SPEC.md contain zero hits** for run-controller / TaskPeek / ClaudeChat / stream-json. The design record is the `DECISIONS.md` table (§2.5). SPEC §41 covers scheduled messages.
- **Mounts.tsx is rclone storage mounts, not chat.** Chat mounting is `apps/claude/ChatMount.tsx`.
- `project-queue`, `new-task-*`, `sidebar-tasks` exist only as test files (code lives in tasks-lib.ts, NewJobModal.tsx, platform/lib/queue.ts).

## 1. Module inventory

### 1.1 Size of apps/claude (lines)

| dir | src | test | css |
|---|---|---|---|
| root | 5,861 | 4,483 | |
| protocol | 8,936 | 8,490 | |
| ui | 16,555 | 16,968 | |
| ann | 6,856 | 6,089 | |
| shots | 3,197 | 3,862 | |
| sched | 2,858 | 2,614 | |
| pane | 2,455 | 1,683 | |
| params | 402 | 408 | |
| live | 313 | 559 | |
| styles | | 385 | 6,746 |
| **total** | **47,433** | **45,541** | **6,746** |

Shell task files: ~30.2k src + ~10k CSS (`styles/tasks.css, task-peek.css, task-cards.css, new-task.css, schedule.css`).

### 1.2 apps/claude root

| file | lines | purpose | deps | endpoints / storage |
|---|---|---|---|---|
| ClaudeChat.tsx | 5062 | Root `.chat-root`; layouts split / chat-only / compact / peek / narrow; boot (resume `?run=` or landing), typer, Escape, wiring of every subsystem | ann, live/watch, pane, params, protocol/*, sched/*, shots, ui/*; `@platform/lib/{api,capture-audio,clock,drafts,queue,router}`, `@platform/ui/ChatFrame`, `@shell/tasks-lib` | /api/claude-sessions/history, /api/fs/stat, /api/prefs, /api/run, /api/schedule, /api/tasks; LS `CHAT_ACTIVITY_KEY` (cross-tab poke) |
| ChatMount.tsx | 328 | Native vs legacy-iframe switch; `React.lazy(ClaudeChat)` in an error boundary falling back to iframe; memory params store seeded with session_id/run/msg/model/effort | ClaudeChat, feature-flag, params/store, ChatFrame | — |
| feature-flag.ts | 335 | Tri-state `native_chat_enabled` (default ON since 2026-09-17; env `FUSED_RENDER_NATIVE_CHAT`), plus `queue.enabled` | api, clock | GET /api/prefs; LS `QUEUE_FLAG_BROADCAST_KEY` |
| legacy-src.ts | 109 | Six legacy URLs `/render?path=<tpl>&_file=<target>&chat_only=1[&compact=1|&peek=1]&session_id=…` | — | — |
| index.ts | 27 | Barrel (type-only ClaudeChat export keeps markdown out of shell entry chunk) | | |

Mount sites (6): TaskCards wall + popup, explorer file sidebar (Preview→PreviewSidebar), ListingPreviewPane, canvases CanvasWorkspace, explorer content pane (`_mode=claude`), TaskPeek.

### 1.3 protocol/ — pure TS, no React/DOM (8.9k)

| file | lines | purpose |
|---|---|---|
| agent.ts | 231 | `runAgent(dir, action, fields)` → `runPy("<dir>/agent.py", {action,...})` → POST /api/run. Per-key supersede (older call aborted, promise never settles). `AgentNeedsInstall`, `AgentError`. Attribution headers (page, X-Fused-Target, call id, X-Fused-Supersedes). `resolveAgentDir(file)` = dirname of `statPath(file).templates.find(mode==="claude").path`. Also `runAppEntry` (app.py), `runArtifacts` (artifacts.py) |
| types.ts | 942 | Wire types for all agent.py actions, segments, PermissionRow, question/plan inputs, PollResponse, Quota, Activity, InboxMessage, TurnBreak |
| run-controller.ts | 3661 | Run loop: sendMessage / pollLoop / sendFollowUp / stopRun / syncPermissions / answerAppState / loadHistory / adoptLiveRun / resumeRun. `createChatController(deps)`. Rules: no cursor; D687 slicing after mid-turn follow-up; seats (only newest loop clears `run`); failed poll keeps `?run=`; open cards pinned below prose. Consts POLL_MS=400, FOLLOWUP_WAIT 15×200ms, APP_STATE_NULL_POLLS=5, UNKNOWN_RUN_RETRIES=5×700ms, ADOPT_LAPS=8. Imports `@platform/lib/api` (decideThroughQueue, markTaskIdle, markTaskRunning, scheduleMessage), drafts, tasksChanged, ../feature-flag. LS `turnStartKey(runId)` |
| controller-api.ts | 743 | UI contract. `ChatState {file, sessionId, runId, lastRunId, status, turns, permissions, appState, skills, working, trouble, permissionMode, queued, inbox, historyLoading, adopting, transcript, context, ownRunEndedAt, repaired, transcriptGen, rev}`; `ChatController` (getState/subscribe, sendMessage, sendFollowUp, stopRun, decidePermission, answerQuestion, decidePlan, dismissCard, answerAppState, openSession, resumeRun, adoptLiveRun, refreshHistory, setExternalWorking, addNote, reportTrouble, newChat, settleAttachments, dispose); `ControllerDeps` injection seam (run, history, historyCache, schedule, sleep, model, effort, hasPane, appStateBlock, callbacks) |
| segments.ts | 628 | Collapsible grouping, streaming tail, reconcile, pollBody |
| wire.ts | 496 | ONLY client-side wire grammar: compose/strip `<live-app-state>`, `<pane-shot>`, `<annotations>` blocks; U+2063 marker words; interrupt marks |
| history.ts | 259 | historyToTurns, session titles, sharedHistoryCache; GET /api/claude-sessions/history |
| summaries.ts | 421 | Tool-chip summaries, permission labels/choices, question/plan models |
| trouble.ts | 222 | Error → trouble card (uses @platform/lib/trouble) |
| sessions.ts | 216 | Recent-task subscription (/api/tasks + /api/tasks/changes; uses @shell/tasksPulse) |
| markdown.ts | 191 | renderMd (marked+DOMPurify), enhanceCodeBlocks (hljs + copy) — sole innerHTML funnel |
| typer.ts | 193 | rAF typewriter |
| inbox.ts | 137 | Undrained follow-up bubbles |
| snapshots.ts | 256 | file-history checkpoint list / plan / revert (agent actions) |
| artifacts.ts | 138 | artifacts.py list |
| recap.ts | 122 | GET /api/claude-sessions/recap?file&session_id&for_uuid |
| quota.ts | 80 | Plan-limit text, continue-after-limit |

### 1.4 ui/ — React (16.6k)

- Transcript core: Transcript.tsx 1045, Turn.tsx 514, SegmentView 357, ToolChip 345, ThinkingView, NoticeView, MarkdownView, Caret, WorkingLine 208, cardPolicy.ts 146 (all folded, D299), stamp.ts, RecapFold, useRepairScroll.
- Cards: CardStack, PermCard 194, QuestionCard 735 (POST /api/tasks/queue/decide), PlanCard 156 — shadcn button/checkbox/radio-group/tabs.
- Composer: Composer.tsx 2286, composer-defaults.ts 621, ModelSelect, EffortSelect, PermissionSelect, PillSelect (popover), ContextMeter + context-window.ts 447, fit.ts + useFitStrip, outbox.ts, sendMerge.ts, SchedButton 535, SchedConfirm. Composer: /api/tasks/changes; imports @platform/lib/{autoGrow,drafts,notifications}, @shell/tasksPulse.
- Landing: Home, HomeCard, Lists.tsx 573 (tabs Recent/Artifacts/Snapshots), list-rows.ts, lists-visibility.ts, useRecentTasks.ts 528 (/api/tasks + long-poll; LS `SEED_STASH`), ArtifactRow, ArtStrip, useArtifacts, Snapshots, SnapRow 319, useSnapshots, useLandingReads. Lists imports @shell/ScheduleTaskViews + @shell/tasks-lib.
- Chrome: Topbar 314 (imports @shell/TaskPeekWho, tasks-lib, ScheduleTaskViews), Kebab 622 (terminal / archive / erase; dropdown-menu, @platform/ui/EraseTaskModal), TroubleView 302, Waiting 351, SchedBlock 175.
- Attachments: AttachTray, AttachIcon (lucide), attachApi.ts (/api/fs/raw), useAttachments 411, Receipts, SentPop + ShotViewer (@platform/ui/modal/Modal), AnnStrip.
- Misc: useAwayRecap 288, useLimitWord, useDismissOnWindow, frameClock, debug-sent (LS `fused-render.debug`).

### 1.5 Other subsystems

- **pane/ (2.5k)**: AppPane.tsx 546 — left pane iframe of the target's `/render` view, same-origin `contentDocument`, NO postMessage. paneUrl.ts 347 — kind decision: app folder (app.py entry, `<meta name="fused-app">`, D301) / file (default view, switchable `leftmode`) / ordinary folder = no pane (D239). appState.ts 619 — `<live-app-state>` snapshot (push with send; pull via agent's app_state MCP tool); /api/fs/upload. useSplit, useNarrowView, LeftModePicker (dropdown, AppStar, git "Source Control" / `_listing` labels), ViewToggle, SplitDivider, useAppStateResponder.
- **ann/ (6.9k)**: "Comment" mode (D298/D306/D343–346): pins, popover, bar, geometry, target, wire-target, layer, overview, store; rec.ts 870 + transcribe.ts 497 = spoken walkthrough (/api/ai/transcribe, /api/jobs); useAnnotations.ts 1105 coordinator. Needs pane/, shots/capture, @platform/lib/capture-audio.
- **shots/ (3.2k)**: dom-capture 557, xo-capture 402 (cross-origin tab capture), native-capture 295 (/api/capture/shot-region), encode 445 (WebP→PNG ladder), attach 737 (/api/fs/raw, shots_dir, image_to_png), dir, capture.
- **sched/ (2.9k)**: scheduled.ts 1009 (/api/schedule, /api/schedule/cancel; composer block + attach), useSchedule.ts 995, waiting.ts 689 (project-queue waiting bubbles), queue-leader.ts.
- **live/watch.ts (313)**: standing watch — storage-event poke, visibility/focus, 5 s interval (skipped hidden).
- **params/ (402)**: createUrlParamsStore / createMemoryParamsStore. Keys: session_id, run, msg, model, effort, permission, leftmode, paneview, annotations, annmode, split ratio.
- **styles/ (6.7k CSS)**: plain CSS, `--c-*` tokens scoped to `.chat-root`: chat 660, transcript 2390, composer 1556, home 603, sched 559, pane 363, ann 516, hljs 99. Tailwind nearly unused here.

### 1.6 shell/ task files

| file | lines | surface | deps / endpoints / storage |
|---|---|---|---|
| Scheduled.tsx | 1438 | /tasks: List, Board, Calendar, Cards; filters; New task modal; peek host | NewJobModal, ScheduleCalendar, ScheduleTaskViews, TaskCards, TaskPeek, TaskPeekFrame, tasks-lib, tasksPulse, @apps/claude; /api/tasks + changes; LS `VIEW_KEY` |
| ScheduleTaskViews.tsx | 5864 | List + Board rows, lanes, drag/drop, hover actions | draft-run, tasks-lib, tasksPulse, drafts, tasksChanged, @apps/claude/feature-flag; LS `LANE_CHOICE_KEY`, `LIST_MEMORY_KEY` |
| ScheduleCalendar.tsx | 1348 | Calendar, one chip/task/day | LS `RANGE_KEY` |
| TaskCards.tsx | 1235 | Cards wall: grid of compact ChatMounts, 6/page (`CARD_PAGE`), modal popup; param-boundary isolation | ChatMount, ChatFrame, Modal; LS `SELECTED_KEY` |
| TaskPeek.tsx | 1438 | Notion-style right slide-in panel with peek ChatMount, own header, resize seam, ↑/↓ | @apps/claude/protocol/agent, ContextMenu, esc-stack, sidebarstate |
| TaskPeekFrame.tsx | 176 | Flex-row host (on /tasks and /apps/<folder>?_tab=tasks) | |
| task-peek-store.ts | 1722 | Module store: open id, width, sidebar give-way, `?peek=` | LS `PEEK_WIDTH_KEY`, `PEEK_AUTOCOLLAPSE_KEY` |
| TaskPeekWho.tsx | 114 | Task identity chip | |
| tasks-lib.ts | 5544 | 198 pure exports: status/columns, drafts, read/unread, lanes, sorts, filters, intents, cardsForTasks, attentionRows | /api/tasks/pulse, /api/tasks/erase, /api/schedule/resend |
| tasksPulse.ts | 1071 | One shared poll for sidebar + listing feed; pulse 10 s/30 s; long-poll changes wait=25 | LS `TASKS_SEEN_KEY` |
| task-status-notify.ts + useTaskStatusNotify.ts | 348+121 | Toasts: in_progress→done (retained, clickable), →blocked, →needs_attention | notifications, presence |
| NewJobModal.tsx | 5646 | New task (GCal-style): title, when, folder, repeat/cron, images, run settings | @apps/explorer/listing/*; /api/claude-sessions, /api/config, /api/recents |
| schedule-lib.ts | 2050 | Schedule rules, chips; re-exports legacy-src | |
| ActivityDock.tsx | 314 | Activity chip (jobs/engines). Task runs excluded (D661) | not task UI |
| GlobalSidebar.tsx (task parts) | ~60/850 | Tasks entry: running dot, "N running" shimmer, waiting label | tasksPulse |
| Home.tsx (task parts) | ~60/584 | Claude Sessions strip (/api/claude-sessions/home), ClaudeHealthStrip | |
| App.tsx | 1173 | Routes /tasks → lazy Scheduled; /apps/<folder>?_tab=tasks → AppPage; /claude-config | |
| small | | TasksSkeleton, peek-preview (/api/current-apps), draft-run, task-peek-flag, task-notify-terminal-flag, EraseTaskModal (→ platform/ui, /api/tasks/erase), row-fit, useMissingFolders, useMarginWheel | |

### 1.7 platform deps

- lib/api.ts (5819 monolith): runPy, statPath, rawUrl, getTasks, getTaskChanges, getTasksPulse, queue verbs, scheduleMessage, prefs.
- drafts.ts 1733 (/api/drafts, /api/drafts/chat/<key>, /api/drafts/task/<id>), router 763, notifications 861, jobs 1113, capture-audio 675, queue 423, trouble 377, layout-codec 325, claude-defaults 244 (LS broadcast key), frame-focus 183, presence 525, tasksChanged 66 (window event), clock, task-id, usage-limit, model-vocab, utils (cn).
- ui: ChatFrame 254 (legacy iframe host), modal/Modal 472, TroubleCard 227, EraseTaskModal 149, ContextMenu, Skeleton, ClaudeMark, AppStar.
- shadcn over @base-ui/react: popover, dropdown-menu (Menu), tabs, collapsible, radio-group, checkbox, button (cva), skeleton.

### 1.8 Dependency graph

```
shell/App.tsx
 ├─ Scheduled (/tasks) ─┬─ ScheduleTaskViews ─ tasks-lib ─ schedule-lib ─ apps/claude/legacy-src
 │                      ├─ ScheduleCalendar
 │                      ├─ NewJobModal ─ apps/explorer/listing/* (pure helpers)
 │                      ├─ TaskCards ─── ChatMount (grid, compact)
 │                      └─ TaskPeekFrame ─ TaskPeek ─ ChatMount (peek) + task-peek-store
 ├─ GlobalSidebar ─ tasksPulse ─ tasks-lib        useTaskStatusNotify ─ task-status-notify
 └─ explorer / canvases / AppPage ─ ChatMount

apps/claude/ChatMount ─lazy─> ClaudeChat
 ClaudeChat
  ├─ protocol/run-controller ─ agent ─ @platform/lib/api.runPy ─> POST /api/run (agent.py)
  │                           ├ history, segments, wire, trouble, quota, controller-api
  │                           └ @platform/lib/{drafts,tasksChanged}, ../feature-flag
  ├─ ui/*  (Transcript, Turn, SegmentView, cards, Composer, Home/Lists, Topbar, Kebab)
  │        └─ shell back-edges: @shell/{tasks-lib, tasksPulse, ScheduleTaskViews, TaskPeekWho}
  ├─ pane/* ─ AppPane iframe(/render …) ─ appState
  ├─ ann/*  ─ shots/capture, capture-audio, transcribe(/api/ai/transcribe, /api/jobs)
  ├─ shots/* ─ /api/fs/raw, /api/capture/shot-region
  ├─ sched/* ─ /api/schedule, project queue (@platform/lib/queue)
  ├─ live/watch  (storage event + 5 s interval)
  └─ params/store (url | memory)
```

apps/claude is NOT self-contained: `scripts/check-boundaries.mjs` lets it import shell modules (tasks-lib 5.5k, tasksPulse 1k, ScheduleTaskViews 5.9k, TaskPeekWho) plus the api.ts monolith.

## 2. Wire protocol, state, coupling, decisions

### 2.1 Chat transport

`POST /api/run` body `{py: "<tplDir>/agent.py", params: {action, ...string fields}}` (nested data JSON-stringified). Actions:
`start poll decide app_state sessions live_run defaults history snapshots snapshot_plan snapshot_revert shots_dir image_to_png terminal_command cancel live_host send`. Plus `app.py` (app entry) and `artifacts.py`.

Key requests: start `{file, message, session_id("" = new), model, effort, permission_mode, has_pane "0"|"1"|"", read_dirs JSON, draft_key?, queue_claim?}`; poll `{run_id, file, native?"1", queue?}`; decide `{run_id, request_id, decision allow|deny, scope once|session, mode ""|acceptEdits|auto, answers?, custom?, note?}`; send `{run_id, message, read_dirs, model, effort, permission_mode, queue_claim?}`; cancel `{run_id, queued?}`.

Poll response:
```
{text, done, session_id, error, tokens, phase(thinking|composing|tooling|requesting|retrying|awaiting),
 message, permissions[], app_state[], mode, skills[], retry, retry_total, retry_status, quota,
 cancelled, tasks_pending, activity{tool,tools_open,tool_input_bytes,thinking_tokens,hook,tasks,agent_rows},
 segments[], window, context, turn_breaks[], inbox[]}
segment: text{text} | thinking{text} | tool{id,name,input,status running|ok|error,output≤4000,images[]} | notice{text,status}
PermissionRow: {id, tool, tool_use_id?, input, created_at, decision ""|allow|deny|expired, scope, mode, answers, held?}
```

Permissions (D161): CLI `--permission-prompt-tool` → stdio MCP `permission_server.py` writes `perm/<id>.req.json`, poll returns them, `decide` writes `.res.json`. Follow-ups: `session_host.py` inbox (D674–D679). Every poll replays the whole turn; client dedupes by id.

### 2.2 Other endpoints

| method, path | used by |
|---|---|
| GET /api/fs/stat?path= | agent-dir lookup (`templates[].mode==="claude"`), pane |
| GET /api/fs/raw?path= | attachments, pane |
| POST /api/fs/upload | app-state, shots |
| GET /api/prefs | native-chat + queue flags |
| GET /api/claude-sessions/history, /recap, /liveness (legacy), /home, /summaries, /triage, /defaults; GET /api/claude-sessions | history, recap, Home strip, New task |
| GET /api/tasks; long-poll GET /api/tasks/changes?since=&wait=25; GET /api/tasks/pulse | Recent list, Tasks page, sidebar |
| POST /api/tasks/{archive,unarchive,erase,delete,read,idle,running,settings}; /api/tasks/<key>/messages; /api/tasks/scheduled?from&to | kebab, peek, calendar |
| POST /api/tasks/queue/{admit,decide,force,skip} | project queue |
| /api/schedule (GET/POST), /cancel, /resend, /run-now, /restore, /queue, /queue/cancel, /shot, /events, /events/ack | scheduling |
| /api/drafts, /api/drafts/chat/<key>, /api/drafts/task/<id> | draft sync |
| POST /api/capture/shot-region, /api/capture/start; /api/ai/transcribe; /api/jobs | shots, voice walkthrough |

Legacy template's direct fetches (everything else via fused.runPython): /api/fs/stat (L5412, 10712), /api/capture/shot-region (9897), /api/prefs (12639), /api/tasks (12713, 13192, 17090), /api/tasks/archive|unarchive (13221), /api/tasks/erase (13344), /api/schedule/cancel (17388), /api/schedule (17462), /api/claude-sessions/liveness (17771), /api/tasks/changes long-poll (18457).

### 2.3 Client state

- Chat state in URL params (url store) or memory store (cards/peek).
- LS (native): CHAT_ACTIVITY_KEY, QUEUE_FLAG_BROADCAST_KEY, CLAUDE_DEFAULTS_BROADCAST_KEY, turnStartKey(runId), SEED_STASH, fused-render.debug.
- LS (shell): TASKS_SEEN_KEY, PEEK_WIDTH_KEY, PEEK_AUTOCOLLAPSE_KEY, SELECTED_KEY, VIEW_KEY, RANGE_KEY, CAL_RANGE_KEY, LANE_CHOICE_KEY, LIST_MEMORY_KEY, RECENTS_KEY, sidebarstate KEY.
- Legacy: sessionStorage `fused:chatdraft:<FILE>`; LS CHAT_ACTIVITY_KEY, SCHEDULE_VIEW_KEY.

### 2.4 UI surfaces

Chat: top bar with running shimmer (D453) + kebab; transcript of folded tool/thinking/notice chips (D246, D299); permission / question (D247, "Other" row D407) / plan (D248) cards; composer with model / effort / permission pills, schedule button, context meter, attachment tray; landing with Recent / Artifacts / Snapshots tabs; split left pane with live app; Comment mode pins/popover/bar + voice walkthrough; recap fold; schedule block; waiting bubbles; trouble cards (D300, D328).
Tasks: List / Board / Calendar / Cards wall; side peek; New task modal; sidebar pulse; toasts.

Legacy template.html DOM ids (same surface set): left, leftbar, leftview, leftframe, annhl/annpins/annpop, divider, chat, anntools, anncta, viewshot, annbtn, annrec, kebab (terminal/archive/delete), leftmode, topbar, session, runmark, logwrap, log, schedblock, inputbox, box, model, effort, perm, schedbtn, artstrip, home, homebox, lists/listtabs (recent, artifacts, snaps), schedpop, shotview, sentpop, erasedlg. 341 top-level functions.

Legacy section map (line starts): CSS split 118, ann 141, left bar 378, shot viewer 991, sentpop 1142, erase dlg 1183, chat pane 1258, task row 1513, perm cards 2032, question 2139, plan 2334, tool timeline 2374, composer 2700, markdown 3079, home 3238, narrow 3697. JS: vendor loader 4468, live app state 4662, whole-pane shot 4743, left-view picker 5475, no-pane 5702, left pane 5838, divider 5883, annotations 5926–8990 (walkthrough 6636, annrec 7787), shots 8991–10660, viewer 10661, sentpop 10963, paste/drop 11326, selectors 11805, schedule button 11943, header name 12654, tail follow 12741, ?msg scroll 12832, kebab 13101–13460, log rendering 13461, perm cards 13746, markdown 14940, typer 15060, tool timeline 15150, app_state pull 15737, stop 15843, follow-ups 15989, send+poll 16177, scheduled send attach 16802, schedule block 16868–17460, standing watch 17627, home 18004, landing lists 18320, artifacts 18551, snapshots 18715, boot 19258.

Legacy vs native gaps (legacy has zero matches for): recap, queue/admit, queue/decide, turn_breaks, context meter, held_answers. Template last modified Sep 22, agent.py Sep 25 → drift will grow (native is primary; legacy is escape hatch).

### 2.5 fused-render-only coupling

- Workspace apps / pane: pane/*, app.py entry (D301), LeftModePicker (git "Source Control", `_listing`), canvases host (D355).
- Git: pane mode labels only; snapshot revert is Claude file-history (D194), not git.
- Drafts: /api/drafts in run-controller, Composer, SchedButton; `start.draft_key`.
- Schedule + project queue: all of sched/, queue admit/decide in run-controller, Waiting, SchedBlock, Topbar queue.
- Explorer: NewJobModal imports explorer listing helpers; list-rows, useAwayRecap build explorer URLs.
- Indexing: none.

**Server-side blocker (any option):** agent.py (6,885 lines) imports `appenv, private_dir, procutil, app_entry, file_history` from `templates/shared/`, uses PIL optionally, needs `permission_server.py` + `session_host.py` beside it. Lite's /api/run must exec it with those on sys.path, and /api/fs/stat must return a `templates[]` entry with `mode:"claude"` (for resolveAgentDir). be-map likely owns this.

### 2.6 Decisions that shape the UI (DECISIONS.md)

D161 chat is the permission prompt · D239 ordinary folder = no left pane · D246 transcript renders segments (one renderer for live/history/repair) · D247/D248 question & plan cards · D267→D299 all collapsible cards folded · D289/D291/D304 app sends scheduled messages; scheduling in composer · D298/D306 "Comment" mode, off by default · D300/D328 account-state trouble cards · D307 liveness held as state · D322 a task IS a Claude session (one thread, many messages) · D371 task target read from `<live-app-state>` entry · D415 `done` per turn · D434 task runs now unless a time was picked · D449–D456 recent-chat rows + running shimmer · D661 task runs not in Activity · D674–D696 session host, send-into-inbox, poll cursor/slicing · D740 erase deletes the session transcript.

## 3. Port plan

### 3.1 Third-party packages (React tree)

| package | version | used by |
|---|---|---|
| react, react-dom | 18.3 | all ui/pane/ann/shell |
| marked | 12.0.2 | markdown.ts (same version vendored in legacy `vendor/`) |
| dompurify | 3.4.13 | markdown.ts (same in legacy) |
| highlight.js | 11.11.1 (lib/common) | markdown.ts (same in legacy) |
| @base-ui/react | 1.7 | shadcn popover/menu/tabs/collapsible/radio/checkbox/button |
| lucide-react | 1.34 | AttachIcon, Receipts, shadcn icons |
| clsx, tailwind-merge, class-variance-authority | | cn helper, button variants |
| tailwindcss 4, @tailwindcss/vite, tw-animate-css | | shadcn primitives only |
| toolchain: vite 6, typescript 6, bun, @vitejs/plugin-react | | build + tests |

Built bundle (fused_render/static/shell-dist, 4.2 MB): ClaudeChat-*.js 591 KB (200 KB gz) + css 88 KB (15 KB gz); it imports main-*.js 692 KB (226 KB gz, the shell entry that boots the whole App), vendor 167 KB (53 KB gz), TaskPeekWho 188 KB (63 KB gz), skeleton 68 KB (23 KB gz), main css 447 KB (71 KB gz). Transitive ≈ 2.2 MB raw / ~650 KB gz; cannot mount alone.

### 3.2 Must / optional / drop

Chat half:

| decision | modules | reason |
|---|---|---|
| must | protocol: agent, types, run-controller, controller-api, history, segments, summaries, wire, markdown, typer, trouble, quota, inbox. ui: Transcript, Turn, SegmentView, ToolChip, Thinking, Notice, WorkingLine, Perm/Question/Plan cards, Composer + pills, Topbar, TroubleView, Home + Lists (Recent). params store. CSS chat/transcript/composer/hljs | the conversation |
| optional | pane/ + appState | only if lite shows the app beside chat (needs /render of target) |
| optional | shots/ paste/drop | needs shots_dir, /api/fs/upload, image_to_png |
| optional | Snapshots, Artifacts, recap, ContextMeter, live/watch, Kebab | each one more endpoint |
| optional, costly | ann/ (6.9k) | needs pane + capture + transcribe |
| drop | sched/, SchedButton, SchedBlock, Waiting, queue admit/decide, drafts sync (use sessionStorage like legacy), feature-flag, ChatMount/legacy-src switch, xo-capture, git/_listing pane modes, canvases | no backend in lite |

Tasks half:

| decision | modules | reason |
|---|---|---|
| must (if tasks at all) | list over /api/tasks + changes long-poll; tasks-lib subset (status, title, sort); open task = chat with session_id | minimal task surface |
| cheap once chat exists | TaskCards (grid of chat mounts), TaskPeek + store | both only frame the chat |
| optional | task-status-notify (3 transitions), sidebar pulse | lite has own job-notify policy |
| drop | Board, Calendar, ScheduleCalendar, NewJobModal (5.6k, pulls explorer), schedule-lib, draft-run, ActivityDock, Mounts | no schedule backend; Mounts unrelated |

### 3.3 Options

- **(a) Vite+React in lite, copy tree.** Full parity (recap, queue, turn_breaks, context meter, held answers); bun tests come along. Cost: new build step in lite DMG pipeline; stubs for @shell back-edges; slim api.ts. Transitive ≈ 47k claude + 30k shell tasks + 13k platform ≈ 90k src + 45k tests.
- **(b1) Run legacy template.html as-is under lite /render.** Lowest effort; vanilla; vendored marked/DOMPurify/hljs; lite runtime.js already provides all fused.* it uses. Needs stubs/empty answers for the direct fetches in §2.2. Loses recap, project queue, turn_breaks seams, context meter, held answers; drift vs agent.py grows. Section map in §2.4 makes trimming tractable.
- **(b2) Hand-port React tree to vanilla.** ~47k lines rewrite. Not recommended.
- **(c) Ship built bundle.** Chunk depends on shell main entry; ~2.2 MB raw / 650 KB gz; opaque. Not viable as-is.
- **(d) New standalone chat entry in fused-render** (Vite lib-mode `mountChat()`, sched/queue/drafts stubbed, @shell imports cut). Lite vendors output as static; no build in lite. Cost: cross-repo release coupling.
- **(e) Hybrid.** `bun build` protocol/ (React-free, ~9k + shim for @platform/lib/{api,drafts,tasksChanged}) into one ESM; vanilla UI over `ChatController.subscribe/getState`.

### 3.4 Recommendation

1. First working chat: **(b1)**, accepting the listed feature losses.
2. Parity without a build in lite: **(d)**; (e) if lite team wants to own a vanilla UI.
3. **(a)** only if lite accepts a React + Tailwind 4 + base-ui toolchain; even then copy only the chat half plus must-have task pieces.
4. Any option: agent.py + templates/shared modules must run under lite's /api/run, plus the endpoint stubs. Nothing works until that does.
