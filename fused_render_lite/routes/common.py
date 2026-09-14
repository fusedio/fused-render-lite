"""AI result-frame helpers and the two route guards, copied from
fused-render's server/common.py (the parts the AI modules import)."""
from __future__ import annotations

from fused_render_lite._web import JSONResponse

AI_PROVIDERS = ("local", "apple", "claude")

#: The apple tier's PINNED model ids -> the capability each serves (D700). The
#: OS owns the weights and picks the variant (AFM 3 Core vs Core Advanced by
#: hardware on macOS 27), so there is exactly one id per capability and no
#: version in it. Because none of these carries a `/` or ends in `.gguf`, the
#: shape rule (`server/ai.py::_is_local_model`) would route them to Claude —
#: this table is consulted BEFORE it, and is the one place the id -> provider
#: inference for this tier is spelled out: `fused.ai.text({model: "afm-text"})`
#: and `fused.ai.text({provider: "apple"})` mean the same call.
APPLE_MODELS = {
    "afm-text": "text-generation",
    "afm-speech": "automatic-speech-recognition",
    "afm-embedding": "embeddings",
}


def apple_model_for(capability: str) -> str | None:
    """The apple tier's one id for `capability`, or None when it has none
    (image and video: Apple ships no programmatic model for either)."""
    for model, served in APPLE_MODELS.items():
        if served == capability:
            return model
    return None


def provider_of_model(model: str | None) -> str | None:
    """The tier a PINNED id names, or None when the id is not pinned and the
    shape rule (or the default) decides. Today only the apple ids are pinned."""
    if model in APPLE_MODELS:
        return "apple"
    return None


def ai_result(payload: dict, *, provider: str, model: str, warnings=None,
              usage: dict | None = None, finish_reason: str = "stop",
              request_id: str | None = None, metadata: dict | None = None) -> dict:
    """The ONE result frame every `fused.ai` verb resolves with (RH-11, D632).

    Learn it once: `payload` is the verb's own output key(s) — `text`,
    `images`, `videos`, `text`+`segments`, `embeddings` — and everything
    else is the same on all five. The frame is the AI SDK's `generateText`
    return contract, because that is the shape page authors already know:

      provider          which tier answered ("local" | "apple" | "claude")
      finishReason      "stop" | "length" | "cancelled"
      warnings          [{type: "unsupported-setting", setting, message}]
      usage             per-verb token/unit counts, camelCase, or null
      response          {id, modelId, timestamp} — what actually ran, when
      providerMetadata  {<provider>: {...}} — everything tier-specific that
                        used to sit at top level (seed, snapped size, file
                        paths, seconds). Read it when you need it; the
                        frame does not change shape because of it.

    No top-level `model` and no echoed inputs: `response.modelId` is the
    resolved id, and the SDK's rule that inputs are not echoed is kept —
    what the server snapped, clamped or invented lives in providerMetadata.
    """
    import datetime as _dt
    frame = {
        "provider": provider,
        "finishReason": finish_reason,
        "warnings": list(warnings or []),
        "usage": usage,
        "response": {
            "id": request_id,
            "modelId": model,
            # One format on every verb and both sides of the wire: seconds, Z
            # (`runtime.js`'s `resultFrame` trims toISOString() to the same).
            "timestamp": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "providerMetadata": {provider: dict(metadata or {})},
    }
    frame.update(payload)
    return frame


def ai_usage_tokens(usage: dict | None) -> dict | None:
    """Internal snake-case token counts -> the frame's camelCase `usage`.

    The counter (`ai_metrics`) keeps `input_tokens`/`output_tokens`; the
    wire speaks the SDK's `inputTokens`/`outputTokens`/`totalTokens`. One
    conversion, here, at the boundary — never two vocabularies in one object.
    """
    if not usage:
        return None
    i, o = usage.get("input_tokens"), usage.get("output_tokens")
    if i is None and o is None:
        return None
    return {"inputTokens": i, "outputTokens": o,
            "totalTokens": (i or 0) + (o or 0)}


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _require_fused(x_fused: str | None) -> JSONResponse | None:
    # Guard for the mutating/executing POSTs. Read endpoints are already safe
    # cross-origin because the browser blocks a foreign page from reading our
    # response; but a POST can be fired blind (no-cors fetch) by any website,
    # with no way to read the reply. Requiring a custom request header forces a
    # CORS preflight, which fails cross-origin since we return no CORS headers —
    # so only our own same-origin pages get through. Not authentication (D3
    # stands): it only blocks blind cross-origin POSTs, nothing more.
    if x_fused != "1":
        return _error("missing or invalid X-Fused header", status=403)
    return None

