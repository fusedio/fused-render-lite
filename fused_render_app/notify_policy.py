"""Which job transitions become a native macOS banner, and what they say.

Pure Python, no AppKit: `macapp.py` installs a `jobs.set_transition_hook`
callback that hands every state change `(prev, after)` to `decide` here and
posts whatever comes back through `webnotify.notify`. Keeping the decisions
in this module means they run under pytest on every platform, while the
display half (UNUserNotificationCenter) only works inside the bundled .app —
so this is the ONLY part of the feature tests can pin, and it is written to
carry as much of the behaviour as possible for that reason: everything that
could be a judgement call ("is this worth interrupting the user for", "what
does the banner say") is decided here, and `webnotify` is left with nothing
to decide but how to draw it.

**Mirrors fused-render's tier semantics, not a second opinion on them.** The
producer of a row already chose a notification tier (`jobs.TIERS`) that
governs what the in-app manager does when the row lands. The native banner
follows the same reading so a user never sees the two surfaces disagree:

- A ``silent`` row finishing is not news — a resident model load or unload
  turning "done" says nothing the running row did not — so it gets NO
  banner. ``transient`` is nearly the same for our purposes: its card in
  the app is the only trace it is meant to leave, so a banner that outlives
  the card would be more than the producer asked for.
- ``error`` and ``cancelled`` are ALWAYS news, whatever the declared tier —
  the same promotion `jobs.effective_tier` applies for the panel. A silent
  load that fails is exactly the case a notification exists for.

**One stable identifier per row.** A job's UN identifier is
``IDENTIFIER_PREFIX + job["id"]`` for every banner about that job, so the
"Downloading weights" banner posted at start is REPLACED in place by the
"Done"/"Failed" banner at the end instead of stacking beside it — the
notification centre shows one line per unit of work, the same rule the
manager keeps (SPEC §36). `job_id_from` is the inverse, used by the click
handler to find which row a banner belonged to.

**Start banners are silent (no chime).** A start banner is a courtesy —
"this is going to take a while, you can look away" — and only for the two
families that regularly do (a weights download, an environment install).
A chime there would train the user to ignore chimes; only a banner that
needs them (a question waiting for an answer, a terminal outcome) sounds.
The same silent treatment applies when a row that was WAITING runs again:
the question was answered, so its banner is replaced, not left asking.

**An error banner shows the line that names the failure.** Producers store
what they have — a worker's stderr tail, uv's whole output — and the first
line of that is rarely the sentence a user needs (see `_error_line`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: UN identifier = IDENTIFIER_PREFIX + job id. Distinct from `webnotify`'s
#: own ``"web-"`` prefix for page-raised Notification() banners, which is how
#: its click dispatcher tells the two apart.
IDENTIFIER_PREFIX = "job:"

# The id families this module notifies about. RESTATED as literals rather
# than imported: `ai/supervisor.py` owns most of these (`JOB_PREFIX`,
# `IMAGE_JOB_PREFIX`, `TRANSCRIBE_JOB_PREFIX`, `VIDEO_JOB_PREFIX`,
# `TEXT_JOB_PREFIX`, `BENCHMARK_JOB_PREFIX`) but importing it pulls in the
# whole AI stack, which this leaf module must not do for the same reason
# jobs.py restates `SCHEDULE_JOB_PREFIX` instead of importing schedule.py.
# `sys:ai-claude:` is an inline literal in `routes/ai_relay.py` (its
# `_remote_job`), and `sys:env-install:` one in `envinstall.py`
# (`_mirror_into_jobs`). Note the benchmark family really ends in ``-``.
AI_MODEL_PREFIX = "sys:ai-model:"
AI_IMAGE_PREFIX = "sys:ai-image:"
AI_VIDEO_PREFIX = "sys:ai-video:"
AI_TRANSCRIBE_PREFIX = "sys:ai-transcribe:"
AI_TEXT_PREFIX = "sys:ai-text:"
AI_CLAUDE_PREFIX = "sys:ai-claude:"
AI_BENCHMARK_PREFIX = "sys:ai-benchmark-"
ENV_INSTALL_PREFIX = "sys:env-install:"

#: Everything else — page-raised rows, `sys:schedule:` — is never a banner:
#: a page's own `fused.trackJob()` row is the page's UI to draw, and a
#: scheduled run is drawn on no surface at all by design (see jobs.py `_sweep`).
NOTIFIABLE_FAMILIES = (
    AI_MODEL_PREFIX,
    AI_IMAGE_PREFIX,
    AI_VIDEO_PREFIX,
    AI_TRANSCRIBE_PREFIX,
    AI_TEXT_PREFIX,
    AI_CLAUDE_PREFIX,
    AI_BENCHMARK_PREFIX,
    ENV_INSTALL_PREFIX,
)

#: The families whose START is worth a (silent) banner: the ones that
#: routinely run for minutes with the user elsewhere. An image render or a
#: text completion is something the user is watching; its start says nothing.
START_FAMILIES = frozenset({AI_MODEL_PREFIX, ENV_INSTALL_PREFIX})

TERMINAL_STATES = ("done", "error", "cancelled")

TITLE_MAX = 80
BODY_MAX = 150
FALLBACK_TITLE = "Render App"


@dataclass(frozen=True)
class Banner:
    identifier: str  # IDENTIFIER_PREFIX + job["id"] — stable per row, so UN replaces in place
    title: str
    body: str
    page: str  # job["page"], "" if none
    sound: bool  # False for a start banner, True for waiting/terminal


def _family_of(job_id: str | None) -> str | None:
    """The notifiable family prefix a job id belongs to, or None if not ours."""
    if not isinstance(job_id, str):
        return None
    for prefix in NOTIFIABLE_FAMILIES:
        if job_id.startswith(prefix):
            return prefix
    return None


def job_id_from(identifier: str | None) -> str | None:
    """Inverse of `IDENTIFIER_PREFIX`: the job id a banner was about, or None
    if the identifier is not one of ours (a `web-*` page notification, say).
    A bare prefix with nothing after it names no row and is also None."""
    if not isinstance(identifier, str) or not identifier.startswith(IDENTIFIER_PREFIX):
        return None
    return identifier[len(IDENTIFIER_PREFIX):] or None


def _first_line(text: object) -> str:
    """First non-empty line, whitespace collapsed; "" when there is none.
    Applied BEFORE the `or` fallback chains in `decide`, so a whitespace-only
    field falls through to the next candidate instead of winning as truthy."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed
    return ""


#: A line that names the failure: a Python exception (`RepositoryNotFoundError:
#: 404 …`, `fused_render_app.x.Error: …`) or uv/pip's own `error: …`.
_ERROR_LINE = re.compile(r"^(?:[\w.]*(?:Error|Exception)\b.*:|error:)", re.IGNORECASE)


def _error_line(text: object) -> str:
    """The one line of an error `message` worth 150 characters of banner.

    Producers store what they have: a worker's stderr TAIL (so the first
    line is the middle of a traceback — `8, in _inner_fn` was the first
    banner this shipped), uv's whole stderr, or one clean sentence. Tried
    in order: the first line that names an exception or reads `error: …`;
    else the LAST non-empty line, where a traceback and uv both end with
    the sentence that matters; "" when there is nothing."""
    if not isinstance(text, str):
        return ""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    for line in lines:
        if _ERROR_LINE.match(line):
            return line
    return lines[-1] if lines else ""


def _cap(text: str, limit: int) -> str:
    """Cap to `limit` characters INCLUDING the ellipsis, so the result is
    never longer than the limit UN is given."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def decide(prev: dict | None, after: dict) -> Banner | None:
    """A banner for the transition `prev -> after`, or None for silence.

    `prev` is the public record before the upsert (None when the row was just
    created); `after` the record after it. Called only on state changes and
    on creation (jobs.py's hook contract), never on a progress tick."""
    job_id = after.get("id")
    family = _family_of(job_id)
    if family is None:
        return None

    state = after.get("state")
    prev_state = prev.get("state") if prev else None
    # A row first seen already in a terminal state still had a beginning we
    # never saw: treat "no prev" as non-terminal everywhere below.
    prev_terminal = prev_state in TERMINAL_STATES
    tier = after.get("tier")

    title = _first_line(after.get("title"))
    detail = _first_line(after.get("detail"))
    message = _first_line(after.get("message"))

    if state == "running":
        if prev_state == "waiting":
            # RESUMED. The waiting banner carried a call to action ("approve
            # compiling numpy"); the user answered, and the build it unblocked
            # can run for minutes. Left alone, that banner keeps asking. A
            # silent replacement under the same identifier takes it down
            # without a new chime for what is, to the user, old news.
            body, sound = detail or "Continuing…", False
        else:
            # START. The tier gate is what separates a weights download
            # (trail) from a resident load (silent) on the SAME
            # `sys:ai-model:` id.
            if family not in START_FAMILIES or tier == "silent":
                return None
            if prev is not None and not prev_terminal:
                return None  # running→running: already announced
            body, sound = detail or "Starting…", False
    elif state == "waiting":
        if prev_state == "waiting":
            return None
        body, sound = message or detail or "Waiting for you", True
    elif state in TERMINAL_STATES:
        if prev_terminal:
            return None  # done→error on a reused id is bookkeeping, not an event
        if state == "error":
            body = _error_line(after.get("message")) or detail or "Failed"
        elif state == "cancelled":
            body = "Cancelled"
        else:
            # Success is the one outcome whose newsworthiness the producer
            # decides. `sys:ai-claude:` auto-dismisses its row on success, so a
            # banner would outlive the thing it points at.
            if tier in ("silent", "transient") or family == AI_CLAUDE_PREFIX:
                return None
            body = detail or "Done"
        sound = True
    else:
        return None

    return Banner(
        identifier=IDENTIFIER_PREFIX + job_id,
        title=_cap(title or FALLBACK_TITLE, TITLE_MAX),
        body=_cap(body, BODY_MAX),
        page=after.get("page") or "",
        sound=sound,
    )
