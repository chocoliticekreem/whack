"""W&B Inference (OpenAI-compatible) client, traced in Weave when WANDB_TEAM/WANDB_PROJECT are set.

Shared by personas (generation), simulate (decision points) and report (prose). Strict JSON out:
the model is asked for JSON only, the reply is stripped of fences and repaired by the caller's
validator. Without WANDB_API_KEY every entry point reports `enabled() == False` and callers use rules.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable

try:
    import weave
except Exception:  # weave is optional at runtime; rules mode never needs it
    weave = None

WANDB_BASE_URL = "https://api.inference.wandb.ai/v1"
DEFAULT_MODEL = "Qwen/Qwen3.8-27B"


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    return bool(env("WANDB_API_KEY"))


def weave_project() -> str | None:
    team, project = env("WANDB_TEAM"), env("WANDB_PROJECT")
    return f"{team}/{project}" if team and project else None


def model_name() -> str:
    return env("WANDB_MODEL") or DEFAULT_MODEL


_weave_ready = False
_client: Any = None


def init_weave() -> None:
    global _weave_ready
    if _weave_ready or weave is None or not enabled() or not weave_project():
        return
    try:
        weave.init(weave_project())
        _weave_ready = True
    except Exception as exc:
        print(f"weave.init failed: {type(exc).__name__}", file=sys.stderr)


def client() -> Any:
    global _client
    if _client is None:
        from openai import OpenAI
        kwargs: dict[str, Any] = {"base_url": WANDB_BASE_URL, "api_key": env("WANDB_API_KEY")}
        if weave_project():
            kwargs["project"] = weave_project()
        _client = OpenAI(**kwargs)
    return _client


def traced(fn: Callable) -> Callable:
    """Wrap in weave.op when weave is importable; a plain function otherwise."""
    return weave.op()(fn) if weave is not None else fn


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return text[start:]


def parse_json(text: str) -> Any:
    cleaned = strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Common model slips: trailing commas and a truncated tail.
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        for tail in ("", "}", "]}", "}]}", '"}]}'):
            try:
                return json.loads(cleaned + tail)
            except json.JSONDecodeError:
                continue
    raise ValueError("model reply was not JSON")


def _chat_json(system: str, user: str, max_tokens: int) -> Any:
    res = client().chat.completions.create(
        model=model_name(), temperature=0.3, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system + " Reply with JSON only, no prose, no code fences."},
                  {"role": "user", "content": user}],
    )
    return parse_json(res.choices[0].message.content or "")


chat_json = traced(_chat_json)


def _chat_text(system: str, user: str, max_tokens: int) -> str:
    res = client().chat.completions.create(
        model=model_name(), temperature=0.4, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (res.choices[0].message.content or "").strip()


chat_text = traced(_chat_text)
