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
 *
 * Everything else the full fused-render runtime exposes (capture,
 * fileIndex, daemon, snapshot) is NOT
 * supported: touching it throws "<name> is not supported on fused-render-lite".
 */
(function () {
  "use strict";

  // Unsupported API. Any call, and any member access on an
  // unsupported namespace, throws — the page fails loudly at the exact line
  // that needs a capability lite does not have.
  function unsupported(name) {
    const err = new Error(name + " is not supported on fused-render-lite");
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

  // autoReload: accepted and ignored. A .fused extract never changes under
  // the page, so there is nothing to watch; pages call this at boot and must
  // not die for it.
  function autoReload() {}

  // ---- fused.ai (Claude tier only) ---------------------------------------
  // Same contract as fused-render's fused.ai.text: one options object,
  // the shared result frame {text, provider, finishReason, warnings, usage,
  // response, providerMetadata}, rejections carry `.type`. Streaming is
  // NDJSON over a chunked HTTP response read with fetch's body reader — no
  // socket. No local inference in lite: image/video/transcribe/embed and
  // the local/apple providers reject with type "unavailable".
  function aiError(type, message) {
    const err = new Error(message);
    err.type = type;
    return err;
  }
  function abortSignalOf(opts) {
    const s = opts && opts.abortSignal;
    return s && typeof s.aborted === "boolean" && typeof s.addEventListener === "function" ? s : null;
  }
  function rethrowAbort(e) {
    if (e && e.name === "AbortError") throw aiError("cancelled", "the AI call was cancelled");
    throw e;
  }
  function rejectUnknownOptions(opts, allowed, apiName) {
    const set = new Set(allowed);
    const unknown = Object.keys(opts).filter((k) => !set.has(k)).sort();
    if (!unknown.length) return null;
    const named = unknown.map((k) => "'" + k + "'").join(", ");
    return aiError("bad_request", named + (unknown.length === 1 ? " is not an option" : " are not options")
      + " of " + apiName + "; accepted: " + allowed.slice().sort().join(", "));
  }
  function failWith(error) {
    throw aiError((error && error.type) || "ai_error", (error && error.message) || "AI call failed");
  }

  const TEXT_KEYS = ["prompt", "provider", "model", "systemPrompt", "effort", "history", "raw",
                     "images", "temperature", "maxTokens", "topP", "onChunk", "abortSignal"];

  function aiText(opts) {
    opts = opts || {};
    const unknown = rejectUnknownOptions(opts, TEXT_KEYS, "fused.ai.text");
    if (unknown) return Promise.reject(unknown);
    if (typeof opts.prompt !== "string" || !opts.prompt.trim()) {
      return Promise.reject(aiError("bad_request", "fused.ai.text({prompt}): prompt must be a non-empty string"));
    }
    const body = {};
    for (const k of TEXT_KEYS) {
      if (k !== "onChunk" && k !== "abortSignal" && opts[k] !== undefined) body[k] = opts[k];
    }
    if (body.images !== undefined) {
      const ownPath = ownQuery("path");
      if (ownPath) body.base = ownPath;
    }
    const onChunk = typeof opts.onChunk === "function" ? opts.onChunk : null;
    if (onChunk) body.stream = true;
    const signal = abortSignalOf(opts);
    if (signal && signal.aborted) return Promise.reject(aiError("cancelled", "the AI call was cancelled"));

    const req = fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Fused": "1" },
      body: JSON.stringify(body),
      signal: signal || undefined,
    }).catch(rethrowAbort);

    if (!onChunk) {
      return req.then((res) => res.json().catch(rethrowAbort)).then((data) => {
        if (!data.ok) failWith(data.error);
        return data.result;
      });
    }
    return req.then((res) => {
      const ct = res.headers.get("Content-Type") || "";
      if (!res.ok || ct.indexOf("x-ndjson") === -1) {
        return res.json().catch(rethrowAbort).then((data) => failWith(data && data.error));
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
            if (!finished) failWith({ type: "ai_error", message: "stream ended without a done frame" });
            if (!finished.ok) failWith(finished.error);
            return finished.result;
          }
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();
          lines.forEach(handleLine);
          return pump();
        }, rethrowAbort);
      }
      return pump();
    });
  }

  function aiUnavailable(verb) {
    return function () {
      return Promise.reject(aiError("unavailable",
        "fused.ai." + verb + " is not available on fused-render-lite: no local inference in this build"));
    };
  }
  function aiGet(path) {
    return fetch(path).then((r) => r.json());
  }
  const ai = {
    text: aiText,
    image: aiUnavailable("image"),
    video: aiUnavailable("video"),
    transcribe: aiUnavailable("transcribe"),
    embed: aiUnavailable("embed"),
    cancel: () => Promise.resolve(false),
    models: {
      list: () => aiGet("/api/ai/runtime"),
      catalog: () => aiGet("/api/ai/catalog"),
      load: aiUnavailable("models.load"),
      download: aiUnavailable("models.download"),
      unload: aiUnavailable("models.unload"),
    },
  };

  // ---- the global ---------------------------------------------------------
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
    daemon: unsupportedNamespace("fused.daemon"),
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
