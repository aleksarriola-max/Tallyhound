"""Tiny Ollama client (standard library only). Talks to a model running on this computer."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request



def _from_ollama_host(host: str) -> str:
    """The address OLLAMA_HOST means, read as Ollama reads it: no scheme means http on port 11434 unless one is given,
    and 0.0.0.0 (listen everywhere, as Ollama's FAQ suggests) is reached on this computer."""
    import re
    host = host.strip() or "http://localhost:11434"
    if "://" not in host:
        host = "http://" + host + ("" if re.search(r":\d+$", host) else ":11434")
    return re.sub(r"^(https?://)(?:0\.0\.0\.0|\[::\])(?=[:/]|$)", r"\1localhost", host)


DEFAULT_URL = _from_ollama_host(os.environ.get("OLLAMA_HOST", ""))
DEFAULT_MODEL = "qwen3.5:9b"


class LLMError(RuntimeError):
    """Raised for any problem talking to the model, with a message a person can act on."""


MAX_REPLY = 20 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):          # a model server never needs to redirect; refusing stops tricks
        return None


_OPEN = urllib.request.build_opener(_NoRedirect)


LOOPBACK = ("localhost", "127.0.0.1", "::1")


def public() -> bool:
    """True on a public hosted copy: Streamlit Community Cloud (apps live under /mount/src) or TALLYHOUND_PUBLIC=1.
    TALLYHOUND_PUBLIC=0 turns it off, for example on your own server behind a firewall."""
    flag = os.environ.get("TALLYHOUND_PUBLIC", "").strip()
    if flag in ("0", "1"):
        return flag == "1"
    return __file__.replace("\\", "/").startswith("/mount/src/")


def check_url(url: str) -> str | None:
    """Why this model address is refused, or None. Only http(s), and never link-local addresses such as the cloud
    metadata service (169.254.169.254), so a visitor to a hosted copy cannot make the server fetch them."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
        host = u.hostname or ""
        u.port                                    # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        return "That is not a valid address."
    if u.scheme not in ("http", "https") or not host:
        return "The model address must start with http:// or https://."
    if host.lower() in ("metadata", "metadata.google.internal"):
        return "That address is not allowed."
    if public() and host.lower().strip("[]") not in LOOPBACK:
        # a public demo must not be a way into the network it runs on; named hosts are refused too, so a name that
        # resolves to a safe address at check time and an internal one at fetch time (DNS rebinding) cannot slip in
        return "On the public demo the model must run on this server (localhost). Run Tallyhound on your own computer to use another address."
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError, OSError):
        return None                               # unreachable anyway; the call reports it
    for a in addrs:
        ip = ipaddress.ip_address(str(a).split("%")[0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return "That address is not allowed."
    return None


def _call(url: str, path: str, payload: dict | None, timeout: float) -> dict:
    bad = check_url(url)
    if bad:
        raise LLMError(bad)
    req = urllib.request.Request(url.rstrip("/") + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPEN.open(req, timeout=timeout) as r:
            raw = r.read(MAX_REPLY + 1)
        if len(raw) > MAX_REPLY:
            raise LLMError("The model server sent back far too much data.")
        out = json.loads(raw.decode("utf-8"))
        if not isinstance(out, dict):
            raise LLMError("The model server's reply was not in the expected shape. Is the address a model server?")
        return out
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise LLMError("The model server tried to redirect; use its final address.") from e
        body = e.read(300).decode("utf-8", "replace")
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
    return installed(url, timeout) or []


def installed(url: str = DEFAULT_URL, timeout: float = 2.0) -> list[str] | None:
    """Names of installed models; None when the server cannot be reached (an empty list: it runs with no models)."""
    try:
        if openai_style(url):
            return sorted(m["id"] for m in _call(url, "/models", None, timeout).get("data", []))
        return sorted(m["name"] for m in _call(url, "/api/tags", None, timeout).get("models", []))
    except (LLMError, KeyError, TypeError):
        return None


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
    msg = out.get("message")
    text = msg.get("content") if isinstance(msg, dict) else None
    try:
        if not isinstance(text, str):
            raise ValueError("no text")
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
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError("The model server sent back no message.") from e
        if "tool_calls" in msg:
            msg["tool_calls"] = _calls(msg)
        for c in msg.get("tool_calls") or []:
            fn = c.get("function", {})
            if isinstance(fn.get("arguments"), str):
                try:
                    fn["arguments"] = json.loads(fn["arguments"] or "{}")
                except ValueError:
                    fn["arguments"] = {}
        msg["_wire_calls"] = [dict(c, function=dict(c.get("function", {}),
                                                    arguments=json.dumps(c.get("function", {}).get("arguments", {}))))
                              for c in msg.get("tool_calls") or []]     # OpenAI wants arguments back as a JSON string
        msg.setdefault("content", "")
        if msg["content"] is None:
            msg["content"] = ""
        return msg
    payload = {"model": model, "stream": False, "think": False, "tools": tools, "messages": messages,
               "options": {"temperature": 0, "num_ctx": num_ctx}}
    out = _call(url, "/api/chat", payload, timeout)
    reply = out.get("message")
    if not isinstance(reply, dict):
        raise LLMError("Ollama sent back no message.")
    if "tool_calls" in reply:
        reply["tool_calls"] = _calls(reply)
    return reply


def _calls(msg: dict) -> list[dict]:
    """The well-formed tool calls of a reply; a call that is not an object with a function object is dropped."""
    calls = msg.get("tool_calls")
    return [c for c in calls if isinstance(c, dict) and isinstance(c.get("function", {}), dict)] \
        if isinstance(calls, list) else []
