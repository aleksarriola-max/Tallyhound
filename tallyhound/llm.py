"""Tiny Ollama client (standard library only). Talks to a model running on this computer."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
if not DEFAULT_URL.startswith("http"):
    DEFAULT_URL = "http://" + DEFAULT_URL
DEFAULT_MODEL = "qwen3.5:9b"


class LLMError(RuntimeError):
    """Raised for any problem talking to the model, with a message a person can act on."""


def _call(url: str, path: str, payload: dict | None, timeout: float) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"Ollama said: {body or e.reason}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise LLMError(f"Could not reach Ollama at {url}. Is it running? (start it with: ollama serve)") from e
    except ValueError as e:
        raise LLMError("Ollama sent back something that was not JSON.") from e


def openai_style(url: str) -> bool:
    """An address ending in /v1 is an OpenAI-compatible server (LM Studio, vLLM, llama.cpp, Ollama's /v1)."""
    return url.rstrip("/").endswith("/v1")


def models(url: str = DEFAULT_URL, timeout: float = 2.0) -> list[str]:
    """Names of installed models. Empty list when the server cannot be reached."""
    try:
        if openai_style(url):
            return sorted(m["id"] for m in _call(url, "/models", None, timeout).get("data", []))
        return sorted(m["name"] for m in _call(url, "/api/tags", None, timeout).get("models", []))
    except (LLMError, KeyError, TypeError):
        return []


def chat_json(system: str, user: str, schema: dict, model: str = DEFAULT_MODEL, url: str = DEFAULT_URL,
              timeout: float = 900.0, num_ctx: int = 16384) -> dict:
    """Ask for an answer that fits `schema`. Returns the parsed object."""
    if openai_style(url):
        out = _call(url, "/chat/completions", {
            "model": model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}}, timeout)
        try:
            return json.loads(out["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError("The model's answer was not valid JSON. Try again or use a larger model.") from e
    payload = {"model": model, "stream": False, "think": False, "format": schema,
               "options": {"temperature": 0, "num_ctx": num_ctx},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    out = _call(url, "/api/chat", payload, timeout)
    text = (out.get("message") or {}).get("content", "")
    try:
        return json.loads(text)
    except ValueError as e:
        raise LLMError("The model's answer was not valid JSON. Try again or use a larger model.") from e


def chat_tools(messages: list[dict], tools: list[dict], model: str = DEFAULT_MODEL, url: str = DEFAULT_URL,
               timeout: float = 900.0, num_ctx: int = 16384) -> dict:
    """One turn of a tool-using conversation. Returns the assistant message (may contain tool_calls)."""
    if openai_style(url):
        out = _call(url, "/chat/completions", {"model": model, "temperature": 0, "messages": messages, "tools": tools}, timeout)
        try:
            msg = dict(out["choices"][0]["message"])
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("The model server sent back no message.") from e
        for c in msg.get("tool_calls") or []:
            fn = c.get("function", {})
            if isinstance(fn.get("arguments"), str):
                try:
                    fn["arguments"] = json.loads(fn["arguments"] or "{}")
                except ValueError:
                    fn["arguments"] = {}
        msg.setdefault("content", "")
        if msg["content"] is None:
            msg["content"] = ""
        return msg
    payload = {"model": model, "stream": False, "think": False, "tools": tools, "messages": messages,
               "options": {"temperature": 0, "num_ctx": num_ctx}}
    out = _call(url, "/api/chat", payload, timeout)
    msg = out.get("message")
    if not isinstance(msg, dict):
        raise LLMError("Ollama sent back no message.")
    return msg
