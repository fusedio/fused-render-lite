/*
 * fused-render-lite runtime. Injected into every rendered page (/render).
 *
 * Supported `window.fused` API:
 *   fused.runPython(pyPath, params, opts?) -> Promise<result>
 *   fused.params.get(key) / getAll() / set(key, value, opts?) / onChange(cb)
 *   fused.readFile(path) -> Promise<string>
 *   fused.stat(path) -> Promise<{path,name,is_dir,size,mtime,writable}>
 *   fused.writeFile(path, content, opts?) -> Promise<stat>
 *   fused.rawUrl(path) -> string
 *   fused.ai.text({prompt, ...}) -> Promise<result frame>   (Claude CLI tier only)
 *   fused.ai.models.list() / catalog(), fused.ai.cancel()
 *   fused.uploadFile(path, blob) / fused.mkdir(path) -> Promise<stat>
 *   fused.trackJob(spec) / fused.watchJob(id)  (in-process job rows)
 *   fused.autoReload(false)  no-op; autoReload(true) throws (no live reload here)
 *   fused.daemon.status() / start() / stop() / restart() / setAutostart(bool)
 *   fused.daemon.run(params) / call(path, body) / watch(cb)
 *     A .fused app's own long-running daemon, declared in its pyproject.toml
 *     ([tool.fused-render.app] daemon = "x.py" | main = "y.py"); fused-render's
 *     block, copied verbatim (see background_routes.py).
 *
 * Everything else the full fused-render runtime exposes (capture,
 * fileIndex, snapshot) is NOT
 * supported: touching it throws "<name> is not supported on Render Lite".
 */
(function () {
  "use strict";

  // Unsupported API. Any call, and any member access on an
  // unsupported namespace, throws — the page fails loudly at the exact line
  // that needs a capability lite does not have.
  function unsupported(name) {
    const err = new Error(name + " is not supported on Render Lite");
    err.type = "unsupported";
    console.error("[fused-lite] " + err.message);
    return err;
  }
  function unsupportedFn(name) {
    return function () { throw unsupported(name); };
  }
  function unsupportedNamespace(name) {
    return new Proxy(Object.freeze({}), {
      get(_t, prop) {
        if (prop === Symbol.toPrimitive || prop === "toString" || prop === "toJSON" || prop === "then") {
          return undefined; // let logging/awaiting the namespace itself not explode
        }
        throw unsupported(name + "." + String(prop));
      },
      apply() { throw unsupported(name); },
    });
  }

  // ---- params -------------------------------------------------------------
  // Copied from fused-render's runtime: params live in the URL of the topmost
  // same-origin window (here the /open page, so the address bar carries the
  // .fused path AND the app's state), reserved `_keys` are hidden, and `set`
  // batches history writes.
  function findTarget() {
    let t = window;
    try {
      while (t.parent && t.parent !== t) {
        void t.parent.location.href;
        if (t.parent._fusedParamBoundary) break;
        t = t.parent;
      }
    } catch (e) {
      /* hit a cross-origin ancestor; t is the topmost same-origin one */
    }
    return t;
  }

  const target = findTarget();
  const standalone = target === window;

  function ancestorWindows() {
    const out = [];
    let t = target;
    try {
      while (t.parent && t.parent !== t) {
        void t.parent.location.href; // throws when cross-origin
        t = t.parent;
        out.push(t);
      }
    } catch (e) {
      /* hit a cross-origin ancestor — chain ends */
    }
    return out;
  }

  function isReserved(key) {
    if (key.startsWith("_")) return true;
    if (standalone && key === "path") return true;
    return false;
  }

  function splitSearch(search) {
    const s = (search || "").replace(/^\?/, "");
    const m = /(^|&)_layout=\(/.exec(s);
    if (!m) return { layoutSpan: null, rest: s };
    const start = m.index + m[1].length;
    let i = start + "_layout=(".length;
    let depth = 1;
    while (i < s.length && depth > 0) {
      if (s[i] === "(") depth++;
      else if (s[i] === ")") depth--;
      i++;
    }
    return {
      layoutSpan: s.slice(start, i),
      rest: (s.slice(0, m.index) + s.slice(i)).replace(/^&|&$/g, ""),
    };
  }

  const HISTORY_MIN_INTERVAL_MS = 400;
  let pendingDelta = null; // Map<key, value> | null
  let pendingPath = null;
  let historyTimer = null;
  let lastHistoryWrite = 0;

  function pendingIsStale() {
    return pendingPath !== null && pendingPath !== target.location.pathname;
  }

  function applyDelta(search, delta) {
    if (!delta || delta.size === 0) return search;
    const { layoutSpan, rest } = splitSearch(search);
    const params = new URLSearchParams(rest);
    for (const [key, value] of delta) {
      if (value === null) params.delete(key);
      else params.set(key, value);
    }
    let out = params.toString();
    if (layoutSpan) out += (out ? "&" : "") + layoutSpan;
    return out ? "?" + out : "";
  }

  function targetSearch() {
    if (pendingIsStale()) return target.location.search;
    return applyDelta(target.location.search, pendingDelta);
  }

  function cancelPending() {
    pendingDelta = null;
    pendingPath = null;
    if (historyTimer !== null) {
      try {
        clearTimeout(historyTimer);
      } catch (e) {
        /* timer already gone */
      }
      historyTimer = null;
    }
  }

  function flushHistory() {
    historyTimer = null;
    if (pendingDelta === null) return;
    const delta = pendingDelta;
    const stale = pendingIsStale();
    pendingDelta = null;
    pendingPath = null;
    if (stale) return; // the page moved on; this write has no entry to land on
    const search = applyDelta(target.location.search, delta);
    if (search === target.location.search) return; // the URL already means this
    const url = target.location.pathname + search;
    lastHistoryWrite = Date.now();
    try {
      target.history.replaceState(target.history.state, "", url);
    } catch (e) {
      console.warn("[fused] history write throttled:", e);
    }
  }

  function currentParams() {
    return new URLSearchParams(splitSearch(targetSearch()).rest);
  }

  let sawGesture = false;
  function markGesture() {
    sawGesture = true;
  }
  const gestureDocs = [document];
  try {
    if (target !== window && target.document && target.document !== document) {
      gestureDocs.push(target.document);
    }
  } catch (e) {
    /* ancestor became unreachable — our own document is enough */
  }
  for (const doc of gestureDocs) {
    try {
      doc.addEventListener("pointerdown", markGesture, true);
      doc.addEventListener("keydown", markGesture, true);
    } catch (e) {
      /* document gone; the gate just stays closed for this frame */
    }
  }

  const listeners = new Set();

  let lastSnapshot = null;

  function fire(snapshot) {
    for (const cb of listeners) {
      try {
        cb(snapshot);
      } catch (e) {
        console.error("[fused] params.onChange listener threw:", e);
      }
    }
  }

  function notifyIfChanged() {
    const snapshot = getAll();
    const serialized = JSON.stringify(snapshot);
    if (serialized === lastSnapshot) return;
    lastSnapshot = serialized;
    fire(snapshot);
  }

  function get(key) {
    if (key === "_file") {
      const own = new URLSearchParams(window.location.search);
      if (own.has("_file")) return own.get("_file");
      const outer = currentParams();
      return outer.has("_file") ? outer.get("_file") : undefined;
    }
    if (isReserved(key)) return undefined;
    const params = currentParams();
    if (params.has(key)) return params.get(key);
    for (const win of ancestorWindows()) {
      const p = new URLSearchParams(splitSearch(win.location.search).rest);
      if (p.has(key)) return p.get(key);
    }
    return undefined;
  }

  function getAll() {
    const result = {};
    const chain = ancestorWindows().reverse();
    chain.push(target);
    for (const win of chain) {
      const search = win === target ? targetSearch() : win.location.search;
      const params = new URLSearchParams(splitSearch(search).rest);
      for (const [key, value] of params) {
        if (isReserved(key)) continue;
        result[key] = value;
      }
    }
    const file = get("_file");
    if (file !== undefined) result._file = file;
    return result;
  }

  function set(key, value, options) {
    if (isReserved(key)) {
      throw new Error(`fused.params.set: '${key}' is a reserved param name and cannot be set`);
    }
    const removing = value === null;
    if (!removing && typeof value !== "string") {
      throw new Error(
        `fused.params.set: value for '${key}' must be a string or null, got ${typeof value}`
      );
    }
    const opts = options || {};
    if (opts.history !== undefined && opts.history !== "replace") {
      throw new Error(
        `fused.params.set: options.history must be "replace", got ${JSON.stringify(opts.history)}`
      );
    }
    if (opts.default !== undefined && typeof opts.default !== "string") {
      throw new Error(
        `fused.params.set: options.default for '${key}' must be a string, got ${typeof opts.default}`
      );
    }
    if (removing && opts.default !== undefined) {
      throw new Error(
        `fused.params.set: options.default is meaningless when removing '${key}'`
      );
    }
    const meansDefault =
      opts.default !== undefined && value === opts.default && get(key) === undefined;
    const newSearch = applyDelta(targetSearch(), new Map([[key, value]]));
    const newUrl = target.location.pathname + newSearch;
    const prevState = target.history.state;
    const unchanged = meansDefault || newSearch === targetSearch();
    if (unchanged) {
    } else if (opts.history === "replace" || !sawGesture || (prevState && prevState.fusedParamEntry)) {
      if (pendingDelta === null || pendingIsStale()) {
        pendingDelta = new Map();
        pendingPath = target.location.pathname;
      }
      pendingDelta.set(key, value);
      if (!historyTimer) {
        const wait = Math.max(
          0,
          HISTORY_MIN_INTERVAL_MS - (Date.now() - lastHistoryWrite)
        );
        if (wait === 0) flushHistory();
        else historyTimer = setTimeout(flushHistory, wait);
      }
    } else {
      const nextState = Object.assign({}, prevState, { fusedParamEntry: true });
      cancelPending();
      lastHistoryWrite = Date.now();
      try {
        target.history.pushState(nextState, "", newUrl);
      } catch (e) {
        console.warn("[fused] history write throttled:", e);
      }
    }
    target.dispatchEvent(new Event("fused:urlchange"));
  }

  function onChange(cb) {
    listeners.add(cb);
    return () => listeners.delete(cb);
  }

  lastSnapshot = JSON.stringify(getAll());

  const hookedWindows = [target, ...ancestorWindows()];
  for (const win of hookedWindows) {
    win.addEventListener("fused:urlchange", notifyIfChanged);
  }

  function onPopState() {
    cancelPending();
    notifyIfChanged();
  }
  const popstateWindows = [window];
  for (const win of hookedWindows) {
    if (popstateWindows.indexOf(win) === -1) popstateWindows.push(win);
  }
  for (const win of popstateWindows) {
    win.addEventListener("popstate", onPopState);
  }

  window.addEventListener("pagehide", () => {
    flushHistory();
    for (const win of hookedWindows) {
      try {
        win.removeEventListener("fused:urlchange", notifyIfChanged);
      } catch (e) {
        /* window already gone */
      }
    }
    for (const win of popstateWindows) {
      try {
        win.removeEventListener("popstate", onPopState);
      } catch (e) {
        /* window already gone */
      }
    }
  });

  // ---- runPython ----------------------------------------------------------
  const inflightByKey = new Map();

  function ownQuery(key) {
    return new URLSearchParams(window.location.search).get(key);
  }

  function pythonError(data) {
    const err = new Error((data.error && data.error.message) || "python error");
    err.type = data.error && data.error.type;
    err.traceback = data.error && data.error.traceback;
    err.stdout = data.stdout;
    err.stderr = data.stderr;
    return err;
  }

  // Stale-request cancellation is on by default: a new call for the same .py
  // (or the same opts.key) aborts the prior in-flight one, and the superseded
  // promise never settles. opts.key === null opts out; opts.signal composes.
  function runPython(pyPath, params, opts) {
    opts = opts || {};
    const key = opts.key === undefined ? pyPath : opts.key;
    const keyed = key !== null;
    const controller = new AbortController();
    if (keyed) {
      const prev = inflightByKey.get(key);
      if (prev) {
        prev._superseded = true;
        prev.abort();
      }
      inflightByKey.set(key, controller);
    }
    let detachSignal = null;
    if (opts.signal) {
      if (opts.signal.aborted) controller.abort();
      else {
        const onAbort = () => controller.abort();
        opts.signal.addEventListener("abort", onAbort);
        detachSignal = () => opts.signal.removeEventListener("abort", onAbort);
      }
    }
    const cleanup = () => {
      if (detachSignal) detachSignal();
      if (keyed && inflightByKey.get(key) === controller) inflightByKey.delete(key);
    };
    const ownPath = ownQuery("path");
    return fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Fused": "1" },
      body: JSON.stringify({ py: pyPath, html: ownPath, params: params || {} }),
      signal: controller.signal,
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.stdout) console.log("[python]", data.stdout);
        if (!data.ok) throw pythonError(data);
        return data.result;
      })
      .then(
        (result) => {
          cleanup();
          if (controller._superseded) return new Promise(() => {});
          return result;
        },
        (err) => {
          cleanup();
          if (opts.signal && opts.signal.aborted) throw err;
          if (controller._superseded) return new Promise(() => {});
          throw err;
        }
      );
  }

  // ---- files --------------------------------------------------------------
  function rawUrl(path) {
    let url = "/api/fs/raw?path=" + encodeURIComponent(path);
    if (path && path[0] !== "/") {
      const ownPath = ownQuery("path");
      if (ownPath) url += "&base=" + encodeURIComponent(ownPath);
    }
    return url;
  }

  function statUrl(path) {
    return rawUrl(path).replace("/api/fs/raw?", "/api/fs/stat?");
  }

  function stat(path) {
    return fetch(statUrl(path))
      .then((res) => res.json().then((data) => ({ res, data })))
      .then(({ res, data }) => {
        if (!res.ok) throw new Error((data && data.error) || "HTTP " + res.status);
        return data;
      });
  }

  function readFile(path) {
    return fetch(rawUrl(path)).then((res) => {
      if (!res.ok) throw new Error("failed to read " + path + " (HTTP " + res.status + ")");
      return res.text();
    });
  }

  function absolutePath(path) {
    if (!path || path[0] === "/") return path;
    const ownPath = ownQuery("path");
    if (!ownPath) return path;
    const dir = ownPath.slice(0, ownPath.lastIndexOf("/") + 1);
    const parts = (dir + path).split("/");
    const out = [];
    for (const p of parts) {
      if (p === "..") out.pop();
      else if (p !== "." ) out.push(p);
    }
    return out.join("/");
  }

  // opts: { expectedMtime } optimistic lock (409 -> err.type "conflict",
  // err.mtime), { create: true } create-only (409 -> err.type "exists").
  // A read-only target rejects with err.type "readonly".
  function writeFile(path, content, opts) {
    const payload = { path: absolutePath(path), content: content };
    if (opts && opts.expectedMtime !== undefined && opts.expectedMtime !== null) {
      payload.expected_mtime = opts.expectedMtime;
    }
    if (opts && opts.create) payload.create = true;
    return fetch("/api/fs/write", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Fused": "1" },
      body: JSON.stringify(payload),
    })
      .then((res) => res.json().then((data) => ({ res, data })))
      .then(({ res, data }) => {
        if (res.status === 409 && payload.create) {
          const err = new Error("file already exists");
          err.type = "exists";
          throw err;
        }
        if (res.status === 409) {
          const err = new Error("file changed on disk");
          err.type = "conflict";
          err.mtime = data && data.mtime;
          throw err;
        }
        if (res.status === 403 && data && data.error === "readonly") {
          const err = new Error("file is read-only");
          err.type = "readonly";
          throw err;
        }
        if (!res.ok) throw new Error((data && data.error) || "HTTP " + res.status);
        return data;
      });
  }

  // ---- uploadFile / mkdir ---------------------------------------------------
  // Same page-facing contract as fused-render; the wire differs (raw body +
  // ?path= instead of multipart) because lite's server has no form parser.
  function fsUrl(route, path) {
    let url = route + "?path=" + encodeURIComponent(path);
    if (path && path[0] !== "/") {
      const ownPath = ownQuery("path");
      if (ownPath) url += "&base=" + encodeURIComponent(ownPath);
    }
    return url;
  }
  function fsFailure(res, data, what) {
    if (res.status === 409) {
      const err = new Error(what + " already exists");
      err.type = "exists";
      return err;
    }
    if (res.status === 403 && data && data.error === "readonly") {
      const err = new Error(what + " is read-only");
      err.type = "readonly";
      return err;
    }
    return new Error((data && data.error) || "HTTP " + res.status);
  }

  function uploadFile(path, blob) {
    return fetch(fsUrl("/api/fs/upload", path), {
      method: "POST",
      headers: { "X-Fused": "1", "Content-Type": "application/octet-stream" },
      body: blob,
    })
      .then((res) => res.json().then((data) => ({ res, data })))
      .then(({ res, data }) => {
        if (!res.ok) throw fsFailure(res, data, "file");
        return data;
      });
  }

  function mkdir(path) {
    const body = { path: path };
    if (path && path[0] !== "/") body.base = ownQuery("path");
    return fetch("/api/fs/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Fused": "1" },
      body: JSON.stringify(body),
    })
      .then((res) => res.json().then((data) => ({ res, data })))
      .then(({ res, data }) => {
        if (!res.ok) throw fsFailure(res, data, "directory");
        return data;
      });
  }

  // ---- trackJob / watchJob ----------------------------------------------------
  // fused-render's contract, minus the shell's download-manager UI: rows live
  // in the server so a reloaded page (or a worker) can follow or cancel work.
  function newJobId() {
    return "j" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }
  function postJob(body, onReject) {
    return fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Fused": "1" },
      body: JSON.stringify(body),
      keepalive: true,
    })
      .then((res) => res.json().then((data) => {
        if (res.ok) return data;
        onReject((data && data.error) || "HTTP " + res.status);
        return null;
      }))
      .catch(() => null);
  }

  function trackJob(spec) {
    spec = spec || {};
    const id = spec.id ? String(spec.id) : newJobId();
    let last = null;
    let settled = false;
    let warned = false;
    let chain = Promise.resolve(null);
    function warnOnce(message) {
      if (warned) return;
      warned = true;
      console.warn("fused.trackJob(" + JSON.stringify(id) + "): " + message);
    }
    function send(fields) {
      if (settled && fields.state === undefined) return chain;
      const body = Object.assign({ id: id }, fields);
      chain = chain.then(() => postJob(body, warnOnce)).then((record) => {
        if (record && record.id === id) last = record;
        return last;
      });
      return chain;
    }
    const handle = {
      id: id,
      update: (fields) => send(fields || {}),
      finish: (detail) => { settled = true; return send({ state: "done", detail: detail === undefined ? "" : detail }); },
      fail: (message) => {
        settled = true;
        const text = message && message.message ? message.message
          : message === undefined || message === null ? "failed" : String(message);
        return send({ state: "error", message: text });
      },
      cancelled: () => { settled = true; return send({ state: "cancelled" }); },
    };
    Object.defineProperty(handle, "cancelRequested", { get: () => !!(last && last.cancel_requested) });
    Object.defineProperty(handle, "state", { get: () => (last ? last.state : "running") });
    send({
      title: spec.title || "Working…", detail: spec.detail || "", kind: spec.kind || "task",
      unit: spec.unit || "", done: spec.done === undefined ? null : spec.done,
      total: spec.total === undefined ? null : spec.total, cancellable: !!spec.cancellable,
      state: "running",
    });
    return handle;
  }

  function watchJob(id) {
    let stopped = false;
    async function get() {
      const res = await fetch("/api/jobs");
      const data = await res.json().catch(() => ({}));
      return (data.jobs || []).find((j) => j.id === id) || null;
    }
    return {
      get,
      async watch(onUpdate, intervalMs) {
        const every = Math.max(200, intervalMs || 700);
        let seen = false;
        let missing = 0;
        for (;;) {
          if (stopped) return null;
          const record = await get().catch(() => null);
          if (record) {
            seen = true;
            missing = 0;
            if (typeof onUpdate === "function") onUpdate(record);
            if (record.state !== "running" && record.state !== "waiting") return record;
          } else if (seen && ++missing >= 5) {
            return null;
          }
          await new Promise((r) => setTimeout(r, every));
        }
      },
      stop() { stopped = true; },
      cancel: () => fetch("/api/jobs/" + encodeURIComponent(id) + "/cancel", {
        method: "POST", headers: { "X-Fused": "1" },
      }).then((r) => r.ok),
    };
  }

  // autoReload(enabled): turning it OFF is a no-op — a .fused extract never
  // changes under the page, so there was nothing to watch anyway, and pages
  // that opt out at boot must not die for it. Turning it ON asks for a live
  // file watcher lite does not have, and the app should know: throw.
  function autoReload(enabled) {
    if (enabled) throw unsupported("fused.autoReload(true)");
  }

  // ---- fused.ai: fused-render's own client code, lifted verbatim -----------
  // Text (Claude / local / apple by model shape), image, video, transcribe,
  // embed, models, cancel. Streaming is NDJSON over chunked HTTP; job-backed
  // verbs poll /api/jobs through watchJob. Two helpers the original took from
  // elsewhere in its runtime are shimmed here:
  function callHeaders(extra, _callId) {
    const h = Object.assign({}, extra || {});
    const ownPath = ownQuery("path");
    if (ownPath) h["X-Fused-Page"] = encodeURIComponent(ownPath);
    return h;
  }
  function noteFsChanged() {}

  function abortSignalOf(opts) {
    const s = opts && opts.abortSignal;
    return s && typeof s.aborted === "boolean" && typeof s.addEventListener === "function"
      ? s : null;
  }
  function cancelledError(what, jobId) {
    const err = new Error(what + " was cancelled");
    err.type = "cancelled";
    if (jobId) err.jobId = jobId;
    return err;
  }
  function rethrowAbort(what) {
    return (e) => {
      if (e && e.name === "AbortError") throw cancelledError(what);
      throw e;
    };
  }
  function resultFrame(payload, f) {
    const meta = {};
    Object.keys(f.metadata || {}).forEach((k) => {
      if (f.metadata[k] !== undefined) meta[k] = f.metadata[k];
    });
    return {
      provider: f.provider || "local",
      finishReason: f.finishReason || "stop",
      warnings: f.warnings || [],
      usage: f.usage === undefined ? null : f.usage,
      response: { id: f.id || null, modelId: f.modelId,
                  timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, "Z") },
      providerMetadata: { [f.provider || "local"]: meta },
      ...payload,
    };
  }
  function frameSegment(s) {
    if (!s || typeof s !== "object") return s;
    const { start, end, words, ...rest } = s;
    const out = { ...rest, startSecond: start, endSecond: end };
    if (Array.isArray(words)) {
      out.words = words.map((w) => ({ word: w.word, startSecond: w.start, endSecond: w.end }));
    }
    return out;
  }
  function startJob(path, body, signal, what) {
    if (signal && signal.aborted) return Promise.reject(cancelledError(what));
    return aiPost(path, body).then((started) => {
      if (!started || typeof started.jobId !== "string" || !started.jobId) {
        const err = new Error(path + " replied with no jobId");
        err.type = "ai_error";
        throw err;
      }
      if (signal && signal.aborted) {
        cancelJob(started.jobId);
        throw cancelledError(what, started.jobId);
      }
      return started;
    });
  }
  function cancelJob(jobId) {
    return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/cancel", {
      method: "POST",
      headers: callHeaders({ "X-Fused": "1" }),
    }).catch(() => {});
  }

  function aiText(opts) {
    opts = opts || {};
    const textKeys = ["prompt", "provider", "model", "systemPrompt", "effort", "history",
                      "raw", "images", "temperature", "maxTokens", "topP"];
    const textUnknownErr = rejectUnknownOptions(opts, textKeys, ["onChunk", "abortSignal"], "fused.ai.text");
    if (textUnknownErr) return Promise.reject(textUnknownErr);
    const prompt = opts.prompt;
    if (typeof prompt !== "string" || !prompt.trim()) {
      const err = new Error("fused.ai.text({prompt}): prompt must be a non-empty string");
      err.type = "bad_request";
      return Promise.reject(err);
    }
    const body = { prompt: prompt };
    if (opts.provider !== undefined) body.provider = opts.provider;
    if (opts.systemPrompt !== undefined) body.systemPrompt = opts.systemPrompt;
    if (opts.model !== undefined) body.model = opts.model;
    if (opts.effort !== undefined) body.effort = opts.effort;
    if (opts.history !== undefined) body.history = opts.history;
    if (opts.raw !== undefined) body.raw = opts.raw;
    if (opts.images !== undefined) {
      body.images = opts.images;
      const ownPath = new URLSearchParams(window.location.search).get("path");
      if (ownPath) body.base = ownPath;
    }
    if (opts.temperature !== undefined) body.temperature = opts.temperature;
    if (opts.maxTokens !== undefined) body.maxTokens = opts.maxTokens;
    if (opts.topP !== undefined) body.topP = opts.topP;
    const onChunk = typeof opts.onChunk === "function" ? opts.onChunk : null;
    if (onChunk) body.stream = true;
    const signal = abortSignalOf(opts);
    if (signal && signal.aborted) return Promise.reject(cancelledError("the AI call"));
    const looksLocal = body.provider === "local"
      || (body.provider === undefined && typeof body.model === "string"
          && (body.model.indexOf("/") !== -1 || /\.gguf$/i.test(body.model)));
    const looksApple = body.provider === "apple"
      || (body.provider === undefined && body.model === "afm-text");
    const wantsServerCancel = !!signal && !onChunk && (looksLocal || looksApple);
    const onAbort = () => {
      aiPost("/api/ai/cancel", looksApple ? { provider: "apple" } : {}).catch(() => {});
    };
    if (wantsServerCancel) signal.addEventListener("abort", onAbort, { once: true });
    const settle = (promise) => wantsServerCancel
      ? promise.finally(() => signal.removeEventListener("abort", onAbort))
      : promise;
    const req = fetch("/api/ai", {
      method: "POST",
      headers: callHeaders({ "Content-Type": "application/json", "X-Fused": "1" }),
      body: JSON.stringify(body),
      signal: signal || undefined,
    }).catch(rethrowAbort("the AI call"));
    function fail(error) {
      const err = new Error(error && error.message);
      err.type = error && error.type;
      if (error && error.jobId) err.jobId = error.jobId;
      throw err;
    }
    if (!onChunk) {
      return settle(req
        .then((res) => res.json().catch(rethrowAbort("the AI call")))
        .then((data) => {
          if (!data.ok) fail(data.error);
          return data.result;
        }));
    }
    return settle(req.then((res) => {
      const ct = (res.headers.get("Content-Type") || "").indexOf("x-ndjson");
      if (!res.ok || ct === -1) {
        return res.json().catch(rethrowAbort("the AI call")).then((data) => fail(data && data.error));
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finished = null;
      function handleLine(line) {
        if (!line.trim()) return;
        const frame = JSON.parse(line);
        if (frame.type === "chunk") onChunk(frame.text);
        else if (frame.type === "done") finished = frame;
      }
      function pump() {
        return reader.read().then(({ done, value }) => {
          if (done) {
            if (buffer) handleLine(buffer);
            if (!finished) fail({ type: "ai_error", message: "stream ended without a done frame" });
            if (!finished.ok) fail(finished.error);
            return finished.result;
          }
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();
          lines.forEach(handleLine);
          return pump();
        }, rethrowAbort("the AI call"));
      }
      return pump();
    }));
  }

  const JOB_STATE_RUNNING = "running";

  function newJobId() {
    return "j" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }

  async function aiPost(path, body, signal) {
    const res = await fetch(path, {
      method: "POST",
      headers: callHeaders({ "Content-Type": "application/json", "X-Fused": "1" }),
      body: JSON.stringify(body || {}),
      signal: signal || undefined,
    }).catch(rethrowAbort("the AI call"));
    const data = await res.json().catch(rethrowAbort("the AI call")).catch((e) => {
      if (e && e.type === "cancelled") throw e;
      return {};
    });
    if (!res.ok) {
      const err = new Error((data && data.error) || res.statusText);
      err.type = res.status === 409 ? "unavailable" : "bad_request";
      throw err;
    }
    return data;
  }
  function rejectUnknownOptions(opts, allowedKeys, extra, apiName) {
    const allowed = new Set(allowedKeys.concat(extra));
    const unknown = Object.keys(opts).filter((key) => !allowed.has(key)).sort();
    if (!unknown.length) return null;
    const named = unknown.map((key) => "'" + key + "'").join(", ");
    const verb = unknown.length === 1 ? "is not an option" : "are not options";
    const accepted = allowedKeys.concat(extra).slice().sort().join(", ");
    const err = new Error(`${named} ${verb} of ${apiName}; accepted: ${accepted}`);
    err.type = "bad_request";
    return err;
  }
  function aiImage(opts) {
    opts = opts || {};
    const imageKeys = ["prompt", "model", "provider", "width", "height", "steps", "guidance", "seed", "image"];
    const unknownErr = rejectUnknownOptions(opts, imageKeys, ["onProgress", "abortSignal"], "fused.ai.image");
    if (unknownErr) return Promise.reject(unknownErr);
    if (typeof opts.prompt !== "string" || !opts.prompt.trim()) {
      const err = new Error("fused.ai.image({prompt}): prompt must be a non-empty string");
      err.type = "bad_request";
      return Promise.reject(err);
    }
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;
    const body = {};
    for (const key of imageKeys) {
      if (opts[key] !== undefined) body[key] = opts[key];
    }
    const ownPath = new URLSearchParams(window.location.search).get("path");
    if (ownPath) body.base = ownPath;
    const signal = abortSignalOf(opts);
    return startJob("/api/ai/image", body, signal, "the image").then((started) => {
      const watcher = watchJob(started.jobId);
      if (signal) {
        signal.addEventListener("abort", () => {
          watcher.stop();
          cancelJob(started.jobId);
        }, { once: true });
      }
      const previewUrl = (job) =>
        started.previewPath && job && job.state === "running"
          ? rawUrl(started.previewPath) + "&step=" + (job.done || 0)
          : null;
      const done = () => resultFrame(
        { images: [{ path: started.path, url: rawUrl(started.path), mediaType: "image/png" }] },
        { provider: started.provider, modelId: started.model, id: started.jobId,
          warnings: started.warnings, usage: { imagesGenerated: 1 },
          metadata: { seed: started.seed, width: started.width, height: started.height,
                      steps: started.steps, guidance: started.guidance, image: started.image,
                      prompt: started.prompt, previewPath: started.previewPath } });
      const tick = onProgress
        ? (job) => onProgress({ ...job, previewUrl: previewUrl(job) })
        : null;
      return watcher.watch(tick).then((record) => {
        if (signal && signal.aborted) throw cancelledError("the image", started.jobId);
        if (!record) {
          return stat(started.path).then(done, () => {
            const err = new Error("the image job is no longer being reported");
            err.type = "ai_error";
            err.jobId = started.jobId;
            throw err;
          });
        }
        if (record.state === "done") return done();
        const err = new Error(
          record.state === "cancelled"
            ? "the image was cancelled"
            : record.message || "the image failed to render",
        );
        err.type = record.state === "cancelled" ? "cancelled" : "ai_error";
        err.jobId = started.jobId;
        throw err;
      });
    });
  }

  function aiVideo(opts) {
    opts = opts || {};
    const videoKeys = ["prompt", "model", "provider", "width", "height", "frames", "steps", "seed", "image"];
    const unknownErr = rejectUnknownOptions(opts, videoKeys, ["onProgress", "abortSignal"], "fused.ai.video");
    if (unknownErr) return Promise.reject(unknownErr);
    if (typeof opts.prompt !== "string" || !opts.prompt.trim()) {
      const err = new Error("fused.ai.video({prompt}): prompt must be a non-empty string");
      err.type = "bad_request";
      return Promise.reject(err);
    }
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;
    const body = {};
    for (const key of videoKeys) {
      if (opts[key] !== undefined) body[key] = opts[key];
    }
    const ownPath = new URLSearchParams(window.location.search).get("path");
    if (ownPath) body.base = ownPath;
    const signal = abortSignalOf(opts);
    return startJob("/api/ai/video", body, signal, "the video").then((started) => {
      const watcher = watchJob(started.jobId);
      if (signal) {
        signal.addEventListener("abort", () => {
          watcher.stop();
          cancelJob(started.jobId);
        }, { once: true });
      }
      const done = () => resultFrame(
        { videos: [{ path: started.path, url: rawUrl(started.path), mediaType: "video/mp4" }] },
        { provider: started.provider, modelId: started.model, id: started.jobId,
          warnings: started.warnings, usage: { videosGenerated: 1 },
          metadata: { seed: started.seed, width: started.width, height: started.height,
                      frames: started.frames, steps: started.steps, image: started.image,
                      prompt: started.prompt } });
      const tick = onProgress ? (job) => onProgress({ ...job }) : null;
      return watcher.watch(tick).then((record) => {
        if (signal && signal.aborted) throw cancelledError("the video", started.jobId);
        if (!record) {
          return stat(started.path).then(done, () => {
            const err = new Error("the video job is no longer being reported");
            err.type = "ai_error";
            err.jobId = started.jobId;
            throw err;
          });
        }
        if (record.state === "done") return done();
        const err = new Error(
          record.state === "cancelled"
            ? "the video was cancelled"
            : record.message || "the video failed to render",
        );
        err.type = record.state === "cancelled" ? "cancelled" : "ai_error";
        err.jobId = started.jobId;
        throw err;
      });
    });
  }

  function aiTranscribe(opts) {
    opts = opts || {};
    const transcribeKeys = ["path", "model", "provider", "language", "task", "initialPrompt",
                            "vad", "diarize", "speakers", "words"];
    const transcribeUnknownErr = rejectUnknownOptions(
      opts, transcribeKeys, ["onProgress", "onChunk", "abortSignal"], "fused.ai.transcribe");
    if (transcribeUnknownErr) return Promise.reject(transcribeUnknownErr);
    if (typeof opts.path !== "string" || !opts.path.trim()) {
      const err = new Error("fused.ai.transcribe({path}): path must be a non-empty string");
      err.type = "bad_request";
      return Promise.reject(err);
    }
    if (opts.diarize && opts.speakers !== undefined && opts.speakers !== null
        && opts.speakers !== "") {
      const MAX_SPEAKERS = 100;
      if (
        !Number.isInteger(opts.speakers) ||
        opts.speakers < 1 ||
        opts.speakers > MAX_SPEAKERS
      ) {
        const err = new Error(
          "fused.ai.transcribe({diarize: true}): 'speakers' must be a " +
            "whole number of people from 1 to " + MAX_SPEAKERS +
            ", e.g. {diarize: true, speakers: 2} — or leave it out and the " +
            "count is estimated from the recording.",
        );
        err.type = "bad_request";
        return Promise.reject(err);
      }
    }
    const onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;
    const onSegment = typeof opts.onChunk === "function" ? opts.onChunk : null;
    const body = {};
    for (const key of transcribeKeys) {
      if (opts[key] !== undefined) body[key] = opts[key];
    }
    const ownPath = new URLSearchParams(window.location.search).get("path");
    if (ownPath) body.base = ownPath;
    const signal = abortSignalOf(opts);
    return startJob("/api/ai/transcribe", body, signal, "the transcription").then((started) => {
      const watcher = watchJob(started.jobId);
      if (signal) {
        signal.addEventListener("abort", () => {
          watcher.stop();
          cancelJob(started.jobId);
        }, { once: true });
      }
      let delivered = 0;
      let offset = 0;
      let pending = "";
      let tailing = false;
      let broken = false;
      const decoder = onSegment && typeof TextDecoder === "function"
        ? new TextDecoder("utf-8") : null;
      const deliver = (raw) => {
        const segment = frameSegment(raw);
        delivered += 1;
        onSegment(segment);
      };
      const readNew = async () => {
        const from = offset;
        const res = await fetch(rawUrl(started.outputPartial), {
          headers: callHeaders({ Range: "bytes=" + from + "-" }),
        });
        if (res.status === 416 || res.status === 404) return;
        if (!res.ok && res.status !== 206) return;
        let bytes = new Uint8Array(await res.arrayBuffer());
        if (res.status !== 206) {
          if (bytes.length <= from) return;
          bytes = bytes.subarray(from);
        }
        offset = from + bytes.length;
        pending += decoder.decode(bytes, { stream: true });
        const lines = pending.split("\n");
        pending = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          let segment;
          try {
            segment = JSON.parse(line);
          } catch (_unparseable) {
            broken = true;
            return;
          }
          deliver(segment);
        }
      };
      let tailChain = Promise.resolve();
      const tail = () => {
        if (!onSegment || !decoder || tailing || broken || !started.outputPartial) {
          return tailChain;
        }
        tailing = true;
        tailChain = tailChain
          .then(readNew)
          .catch(() => {})
          .then(() => { tailing = false; });
        return tailChain;
      };
      const drainPartial = () =>
        tailChain
          .then(() => {
            if (!onSegment || !decoder || broken || !started.outputPartial) return;
            return readNew();
          })
          .catch(() => {});
      const done = () =>
        tailChain
          .then(() => readFile(started.output))
          .then(JSON.parse)
          .then((written) => {
            if (onSegment) {
              for (const segment of (written.segments || []).slice(delivered)) {
                deliver(segment);
              }
            }
            return written;
          })
          .then((written) => resultFrame(
            {
              text: written.text,
              segments: (written.segments || []).map(frameSegment),
              language: written.language,
              durationInSeconds: written.duration,
            },
            { provider: started.provider, modelId: started.model, id: started.jobId,
              warnings: started.warnings, usage: null,
              metadata: {
                path: started.path,
                output: started.output,
                url: rawUrl(started.output),
                outputText: started.outputText,
                outputPartial: started.outputPartial,
                task: started.task,
                speakers: written.speakers,
                estimatedSpeakers: written.estimatedSpeakers,
              } }))
          .catch((cause) => {
            const err = new Error(
              "the transcript could not be read: " + ((cause && cause.message) || cause),
            );
            err.type = "ai_error";
            err.jobId = started.jobId;
            err.cause = cause;
            throw err;
          });
      const onTick = onSegment
        ? (record) => { if (onProgress) onProgress(record); tail(); }
        : onProgress;
      return watcher.watch(onTick).then((record) => {
        if (signal && signal.aborted) throw cancelledError("the transcription", started.jobId);
        if (!record) {
          return done().catch(() => {
            const err = new Error("the transcription job is no longer being reported");
            err.type = "ai_error";
            err.jobId = started.jobId;
            err.output = started.output;
            err.outputPartial = started.outputPartial;
            return drainPartial().then(() => { throw err; });
          });
        }
        if (record.state === "done") return done();
        const err = new Error(
          record.state === "cancelled"
            ? "the transcription was cancelled"
            : record.message || "the transcription failed",
        );
        err.type = record.state === "cancelled" ? "cancelled" : "ai_error";
        err.jobId = started.jobId;
        err.output = started.output;
        err.outputPartial = started.outputPartial;
        return drainPartial().then(() => { throw err; });
      });
    });
  }

  function aiEmbed(opts) {
    opts = opts || {};
    const hasTexts = Array.isArray(opts.texts) && opts.texts.length > 0;
    const hasPaths = Array.isArray(opts.paths) && opts.paths.length > 0;
    if (hasTexts === hasPaths) {
      const err = new Error(
        "fused.ai.embed({texts|paths}): pass exactly one of 'texts' or "
          + "'paths' — a non-empty array of strings",
      );
      err.type = "bad_request";
      return Promise.reject(err);
    }
    const body = {};
    if (hasTexts) body.texts = opts.texts;
    if (hasPaths) body.paths = opts.paths;
    if (opts.model !== undefined) body.model = opts.model;
    if (opts.provider !== undefined) body.provider = opts.provider;
    if (opts.kind !== undefined) body.kind = opts.kind;
    if (hasPaths) {
      const ownPath = new URLSearchParams(window.location.search).get("path");
      if (ownPath) body.base = ownPath;
    }
    const signal = abortSignalOf(opts);
    if (signal && signal.aborted) return Promise.reject(cancelledError("the embedding"));
    return fetch("/api/ai/embed", {
      method: "POST",
      headers: callHeaders({ "Content-Type": "application/json", "X-Fused": "1" }),
      body: JSON.stringify(body),
      signal: signal || undefined,
    })
      .catch(rethrowAbort("the embedding"))
      .then((res) => res.json().catch(() => ({})).then((data) => ({ res, data })))
      .then(({ res, data }) => {
        if (!res.ok || !data.ok) {
          const error = data.error || {};
          const err = new Error(
            error.message || res.statusText || "the embedding failed");
          err.type = error.type
            || (res.status === 409 ? "unavailable" : "ai_error");
          if (error.jobId) err.jobId = error.jobId;
          throw err;
        }
        return data.result;
      });
  }

  const aiModels = {
    list: () => fetch("/api/ai/runtime", { headers: callHeaders({}) }).then((r) => r.json()),
    catalog: () => fetch("/api/ai/catalog", { headers: callHeaders({}) }).then((r) => r.json()),
    load: (model, opts) =>
      aiPost("/api/ai/runtime/load", { model, ...(opts || {}) }),
    download: (model, opts) =>
      aiPost("/api/ai/runtime/download", { model, ...(opts || {}) }),
    unload: (model) =>
      aiPost("/api/ai/runtime/unload",
             typeof model === "string" || model == null
               ? { model } : { capability: model.capability }),
  };
  const ai = {
    text: aiText,
    models: aiModels,
    image: aiImage,
    video: aiVideo,
    transcribe: aiTranscribe,
    embed: aiEmbed,
    cancel: (capability) =>
      aiPost("/api/ai/cancel", capability ? { capability } : {}).then((r) => !!r.cancelled),
  };


  // ---- the global ---------------------------------------------------------
  // Preview flag fused-render's thumbnail renderer stamps onto /render URLs;
  // read here so the fused.daemon block below is verbatim fused-render's.
  const IS_THUMBNAIL = ownQuery("_preview") === "1";

  // ---- background apps (fused.daemon, background_routes.py) ---
  // fused.daemon is the browser control surface for a FOLDER's declared
  // long-running daemon, not this page's own script — every method sends the
  // page's own path as `html`, and the server resolves which app folder that
  // page belongs to, exactly like resolve_py does for runPython. `run` and
  // `call` both reach the daemon itself, through the SAME stable-origin
  // /api/engines/<id>/proxy path a template daemon's traffic already rides
  // (engine_forward is engine-kind-agnostic), using the engine_id a `status()`
  // call cached — a page never computes that id itself. `run(params)` is the
  // `main =` convenience: POST /call with `params` as the body, unwrapping
  // the {ok, result, error, stdout, resolved_py} envelope the shipped worker
  // answers with. `call(path, body)` is for a `daemon =` folder's own routes,
  // proxied and handed back raw — a `main =` folder's single route would
  // just be `call("/call", ...)` minus the unwrap, which is why `call()`
  // refuses a `main =` folder outright (use `run()`) and `run()` refuses a
  // `daemon =` folder outright (use `call()`), each naming the folder's
  // actual declared protocol and the method to use instead. Both bring the
  // daemon up transparently — on a page's first call, and to re-warm it
  // after the idle reaper retires it — rather than requiring an explicit
  // `start()` first: the preview guard below (D507/D508), not a start-first
  // gate, is what actually stops a card thumbnail or hover peek from
  // spawning a daemon, and `engine_forward._forward` already heals a
  // dead-but-running child on any proxied call regardless of which of the
  // two methods reaches it.
  //
  // Run state and autostart are deliberately independent (D511): `stop`
  // kills the running daemon but never touches the persisted autostart flag
  // — if it's on, the server's startup hook (or a later `start`/`restart`)
  // brings it back; if it's off (the default), it stays down until an
  // explicit `start`. `setAutostart` is the ONLY thing that flips that flag,
  // and it never starts or stops anything itself.
  // `_daemonEngineId` is a hash of the FOLDER, so `status()` always resolves one
  // whether or not the app is running — it names WHICH app, not whether one is
  // running. `_daemonKnownRunning` is the separate, actually-gating fact
  // (bring-up reads this, never engine_id's presence, which is always
  // truthy and so cannot tell "not running" from "running"). `_daemonProtocol`
  // is the folder's declared bring-up shape from that same status() payload
  // ("main" | "daemon" | null for a folder with no valid manifest) — it is
  // what lets `run()`/`call()` catch an author calling the wrong one of the
  // two for their folder instead of silently 404ing (a `daemon =` folder
  // under `run()`) or handing back a raw, unwrapped envelope (a `main =`
  // folder under `call()`).
  let _daemonEngineId = null;
  let _daemonKnownRunning = false;
  let _daemonProtocol = null;

  function _noteDaemonPayload(data, marksRunning) {
    if (data && data.engine_id) _daemonEngineId = data.engine_id;
    if (data && "protocol" in data) _daemonProtocol = data.protocol;
    if (marksRunning !== undefined) {
      // start()/restart() succeeding means ensure_background returned a live
      // child (both 502 on any spawn failure, so a 200 here IS "running");
      // stop() succeeding means the daemon is now definitely down.
      _daemonKnownRunning = marksRunning;
    } else if (data && typeof data.running === "boolean") {
      // status()'s own report of the live-child boolean.
      _daemonKnownRunning = data.running;
    }
  }

  // A page must not START a background daemon merely by being rendered
  // (D507, fused-render SPEC §46): in fused-render a card thumbnail or hover
  // peek mounts the entry html live in a sandboxed iframe, stamped with
  // `_preview=1` on its /render URL. Lite renders no thumbnails today, so
  // IS_THUMBNAIL is false for every real open — the guard is kept verbatim
  // so the block stays identical to fused-render's and a future preview
  // surface gets it for free. `start()`/`restart()`
  // obviously spawn; `call()` and `run()` are in scope too — both bring the
  // daemon up transparently when not known running, and even set aside
  // that, engine_forward.py's `_forward` heals a dead-but-running child back
  // to life on ANY proxied call, so a preview render that calls either
  // against an app some other session already started can resurrect its
  // daemon exactly like `start()` would. `stop()` and `setAutostart()` are
  // gated the same way, NOT left
  // open (D508): a card thumbnail mounts `entry_html` live in a sandboxed
  // iframe with `allow-scripts`, so an app whose init path calls
  // `fused.daemon.stop()` (or flips autostart) would change a real user's
  // daemon state just because its card scrolled past or was hovered —
  // `setAutostart(true)` is worse than the old enable bug this guard exists
  // for in the first place, because it survives a server restart. `status()`
  // is the one method deliberately left open (read-only — and the pattern
  // the rejection below points authors at).
  function _daemonRejectPreview(method) {
    return Promise.reject(new Error(
      `fused.daemon.${method}: refused — this page is rendering as a preview ` +
      "thumbnail (a card peek or hover, not a real open), and a page must " +
      "never start a background daemon just by being displayed or hovered. " +
      "Call fused.daemon.status() on load to read state, and call " +
      "start()/restart() only from an explicit user action, e.g. a " +
      "button's click handler."
    ));
  }

  function _daemonPost(path, marksRunning, extraBody) {
    return fetch(path, {
      method: "POST",
      headers: callHeaders({ "Content-Type": "application/json", "X-Fused": "1" }),
      body: JSON.stringify(Object.assign({ html: ownQuery("path") }, extraBody || {})),
    }).then((res) =>
      res.json().then((data) => {
        if (!res.ok) {
          // A failed start/restart/setAutostart is not a state change either
          // way — the daemon's actual state is whatever it already was, so
          // only note the engine_id (still useful for status()), never
          // marksRunning.
          _noteDaemonPayload(data, undefined);
          const err = new Error((data && data.error) || `${path} failed`);
          throw err;
        }
        _noteDaemonPayload(data, marksRunning);
        return data;
      })
    );
  }

  function daemonStatus() {
    const html = encodeURIComponent(ownQuery("path") || "");
    return fetch(`/api/apps/background/status?html=${html}`).then((res) =>
      res.json().then((data) => {
        if (!res.ok) {
          const err = new Error((data && data.error) || "app status failed");
          throw err;
        }
        _noteDaemonPayload(data);
        return data;
      })
    );
  }

  function daemonStart() {
    if (IS_THUMBNAIL) return _daemonRejectPreview("start");
    return _daemonPost("/api/apps/background/start", true);
  }

  function daemonStop() {
    if (IS_THUMBNAIL) return _daemonRejectPreview("stop");
    return _daemonPost("/api/apps/background/stop", false);
  }

  function daemonRestart() {
    if (IS_THUMBNAIL) return _daemonRejectPreview("restart");
    return _daemonPost("/api/apps/background/restart", true);
  }

  function daemonSetAutostart(autostart) {
    if (IS_THUMBNAIL) return _daemonRejectPreview("setAutostart");
    return _daemonPost("/api/apps/background/autostart", undefined,
                       { autostart: !!autostart });
  }

  // Shared bring-up-then-POST mechanics behind both `call()` and `run()`:
  // learn engine_id/protocol/running from one status() fetch when nothing is
  // cached yet, bring the daemon up when it isn't known running (a page's
  // first-ever call, or an app the idle reaper retired since the last poll),
  // then POST to the proxy path and hand back the raw response. Carries NO
  // protocol check — that is each public method's own job, applied before
  // delegating here, so that `run()`'s internal use of this helper can never
  // reject itself against `run()`'s own check.
  function _daemonProxyPost(path, body) {
    const doPost = () =>
      fetch(`/api/engines/${_daemonEngineId}/proxy/${String(path).replace(/^\/+/, "")}`, {
        method: "POST",
        headers: callHeaders({ "Content-Type": "application/json", "X-Fused": "1" }),
        body: JSON.stringify(body || {}),
      }).then((res) => res.json().then((data) => ({ data, httpOk: res.ok })));

    const ready = _daemonEngineId !== null ? Promise.resolve() : daemonStatus();
    const bringUp = ready.then(() => (_daemonKnownRunning ? null : daemonStart()));
    return bringUp.then(() =>
      doPost().then(({ data, httpOk }) => {
        if (!httpOk) {
          const err = new Error((data && data.error) ||
                                `fused.daemon: proxy call to ${path} failed`);
          throw err;
        }
        return data;
      })
    );
  }

  function _daemonWrongProtocolError(method, declared, wantMethod, wantArgs) {
    return new Error(
      `fused.daemon.${method}: refused — this folder declares \`${declared} =\` ` +
      (declared === "daemon"
        ? "and serves its own routes; use "
        : "and the shipped worker serves exactly one route; use ") +
      `fused.daemon.${wantMethod}(${wantArgs}) instead.`
    );
  }

  function daemonCall(path, body) {
    if (IS_THUMBNAIL) return _daemonRejectPreview("call");
    // Nothing cached yet — learn the folder's declared protocol from one
    // status() fetch before deciding, same round trip _daemonProxyPost would
    // need anyway.
    const ready = _daemonEngineId !== null ? Promise.resolve() : daemonStatus();
    return ready.then(() => {
      if (_daemonProtocol === "main") {
        return Promise.reject(
          _daemonWrongProtocolError("call", "main", "run", "params")
        );
      }
      return _daemonProxyPost(path, body);
    });
  }

  // Fields that count as "the daemon's state changed" for watch()'s diff.
  // `running`/`autostart` are the two facts a page's UI actually reflects
  // (a switch, a checkbox); `pid`/`version` catch a restart that leaves
  // `running` true throughout (a crash-and-resurrect, or an explicit
  // restart() from elsewhere) — a page that cached a stale engine call
  // target wants to know that happened too. `engine_id` is deliberately
  // excluded: it is a hash of the FOLDER, stable for the page's whole
  // lifetime, and would never fire on its own.
  const DAEMON_WATCH_FIELDS = ["running", "autostart", "pid", "version"];

  function _daemonStatusChanged(a, b) {
    if (!a || !b) return true;
    for (let i = 0; i < DAEMON_WATCH_FIELDS.length; i++) {
      const f = DAEMON_WATCH_FIELDS[i];
      if (a[f] !== b[f]) return true;
    }
    return false;
  }

  function daemonWatch(callback) {
    if (typeof callback !== "function") {
      throw new TypeError("fused.daemon.watch: callback must be a function");
    }

    // Preview guard (D507/D508's rationale, applied to a READ-only method):
    // watch() is status() underneath, and status() is the one fused.daemon
    // method a thumbnail may legitimately call — but a live poll loop plus
    // two page-level listeners have no business running in a sandboxed
    // preview iframe that gets mounted and unmounted on every hover. Do the
    // one status() read a thumbnail is allowed, hand it to the caller, and
    // return an unsubscribe that has nothing to clean up.
    if (IS_THUMBNAIL) {
      daemonStatus().then(callback).catch(function () {});
      return function unsubscribe() {};
    }

    let last = null;
    let timer = null;

    function poll() {
      return daemonStatus().then(function (data) {
        if (_daemonStatusChanged(last, data)) {
          last = data;
          callback(data);
        } else {
          last = data;
        }
      }).catch(function () {
        // A failed status() read (offline, server restarting) must not kill
        // the watch loop or spam the callback with an error it didn't ask
        // for — skip this tick, the next poll or focus/visibility event
        // tries again.
      });
    }

    // 5s: fast enough that quitting from the tray reads as "immediate" to a
    // human glancing at a foregrounded tab, slow enough to be free — status()
    // is a single in-memory dict read plus one Popen.poll() server-side (no
    // folder walk, no toml parse, see the endpoint's own docstring), and this
    // only ever runs while the tab is visible in the first place.
    const POLL_MS = 5000;

    function stopTimer() {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    }

    function startTimerIfVisible() {
      stopTimer();
      if (document.visibilityState === "visible") {
        timer = setInterval(poll, POLL_MS);
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") {
        poll();
      }
      startTimerIfVisible();
    }

    function onFocus() {
      poll();
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("focus", onFocus);
    poll();
    startTimerIfVisible();

    return function unsubscribe() {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("focus", onFocus);
      stopTimer();
    };
  }

  // `run(params)` is the shipped-worker convenience over the same
  // `_daemonProxyPost` mechanics `call()` uses: a `main =` daemon speaks
  // exactly one route, POST /call with the raw params object as the body,
  // answering the same {ok, result, error, stdout, resolved_py} envelope
  // runPython does — so `run` unwraps that envelope the way runPython does,
  // instead of handing back the raw proxy response `call` gives a
  // `daemon =` author talking to their own routes.
  function daemonRun(params) {
    if (IS_THUMBNAIL) return _daemonRejectPreview("run");
    // Nothing cached yet — learn the folder's declared protocol from one
    // status() fetch before deciding, same round trip _daemonProxyPost would
    // need anyway. Bring-up itself (spawning on the first call, re-warming
    // after the idle reaper retires it) lives entirely in _daemonProxyPost
    // now — run() adds nothing on top of it besides its own protocol check
    // and the envelope unwrap `call()` deliberately leaves raw.
    const ready = _daemonEngineId !== null ? Promise.resolve() : daemonStatus();
    return ready.then(() => {
      if (_daemonProtocol === "daemon") {
        return Promise.reject(
          _daemonWrongProtocolError("run", "daemon", "call", "path, body")
        );
      }
      return _daemonProxyPost("/call", params || {});
    }).then((data) => {
      if (data && data.stdout) console.log("[python]", data.stdout);
      // (fused-render also feeds data.resolved_py to its auto-reload watcher;
      // lite has no live reload, so nothing to watch here.)
      if (!data.ok) {
        const err = new Error(data.error && data.error.message);
        err.type = data.error && data.error.type;
        err.traceback = data.error && data.error.traceback;
        err.stdout = data.stdout;
        throw err;
      }
      return data.result;
    });
  }

  const daemon = {
    status: daemonStatus,
    start: daemonStart,
    stop: daemonStop,
    restart: daemonRestart,
    setAutostart: daemonSetAutostart,
    call: daemonCall,
    run: daemonRun,
    watch: daemonWatch,
  };

  window.fused = {
    env: "local",
    device: "desktop",
    lite: true,
    runPython,
    rawUrl,
    stat,
    readFile,
    writeFile,
    params: { get, getAll, set, onChange },
    // Not supported on lite: every one of these throws when touched.
    uploadFile,
    mkdir,
    trackJob,
    watchJob,
    autoReload,
    snapshot: unsupportedFn("fused.snapshot"),
    daemon,
    ai,
    capture: unsupportedNamespace("fused.capture"),
    fileIndex: unsupportedNamespace("fused.fileIndex"),
  };

  // ---- error overlay ------------------------------------------------------
  function showOverlay(err) {
    const overlay = document.createElement("div");
    overlay.style.cssText = [
      "position:fixed", "inset:0", "z-index:2147483647",
      "background:rgba(20,0,0,0.92)", "color:#ffdede",
      "font-family:ui-monospace,Menlo,Consolas,monospace",
      "font-size:13px", "padding:24px", "overflow:auto",
      "border:4px solid #c0392b", "box-sizing:border-box",
      "white-space:pre-wrap",
    ].join(";");
    const title = document.createElement("div");
    title.style.cssText = "font-size:16px;font-weight:bold;margin-bottom:12px;color:#ff6b6b;";
    title.textContent = (err.type || "Error") + ": " + (err.message || "");
    const pre = document.createElement("pre");
    pre.style.cssText = "margin:0;white-space:pre-wrap;word-break:break-word;";
    pre.textContent = err.traceback || "";
    overlay.appendChild(title);
    overlay.appendChild(pre);
    document.body.appendChild(overlay);
  }

  window.addEventListener("unhandledrejection", (event) => {
    const err = event.reason;
    if (err && err.name === "AbortError") {
      event.preventDefault();
      return;
    }
    if (err && err.traceback) showOverlay(err);
  });
})();
