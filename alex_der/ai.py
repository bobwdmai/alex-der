"""
Ollama AI backend — streaming chat with tool use.
"""

import json
import os
import requests
from typing import Any, Generator

from .tools import TOOL_SCHEMAS

SYSTEM_PROMPT = """\
You are alex-der, a powerful AI coding assistant (part of bob-der2.0).
You help users write, read, edit, debug, and understand code.

You have access to these tools:
- read_file    — read any file with line numbers
- write_file   — create or overwrite a file
- edit_file    — surgically replace an exact string in a file
- bash         — run shell commands
- list_dir     — list directory contents
- grep         — search text in files (uses ripgrep when available)
- find_files   — find files matching a glob pattern
- git          — run git commands
- package      — install/remove/update/check/list/search packages via apt, pip,
                 npm, cargo, snap, gem, or go. Uses sudo automatically for system
                 packages. Always confirm with the user before installing.
- npm_dev      — start/stop/tail an npm dev server in the background
- browse       — fetch a URL as readable text (CLI web browser)
- keyboard     — simulate keyboard input via xdotool
- ask_user     — ask the user a question with an arrow-key picker
- add_tool     — add a new tool to alex-der at runtime (persisted)
- compact_conversation — compress conversation history into a summary

Guidelines:
- Always read files before editing them so you understand the context.
- Prefer edit_file over write_file for existing files — it's surgical and safe.
- Use package to install missing dependencies before running code that needs them.
- After installing a Python package with pip, you can use it immediately in bash.
- For multi-file changes, batch them logically and explain what you're doing.
- Run tests after making code changes when a test command is available.
- Be concise. Don't repeat what you just did at length — one short sentence is enough.
- When you find a bug, fix it directly rather than describing what to do.
- If a task requires multiple steps, do them all, don't stop halfway.
- NEVER make up file contents or tool results. Use the tools to get real data.
"""


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Convert stored conversation messages into the format Ollama actually accepts.

    Ollama differences from OpenAI-style storage:
    - assistant tool_calls: no `id` or `type` wrapper; arguments must be a dict (not a JSON string)
    - tool result messages: only `role` + `content` — no `tool_call_id` or `name`
    """
    out = []
    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant":
            clean: dict[str, Any] = {"role": "assistant", "content": msg.get("content", "")}
            raw_tcs = msg.get("tool_calls")
            if raw_tcs:
                ollama_tcs = []
                for tc in raw_tcs:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    ollama_tcs.append({"function": {"name": fn.get("name", ""), "arguments": args}})
                clean["tool_calls"] = ollama_tcs
            out.append(clean)

        elif role == "tool":
            # Ollama only accepts role + content for tool results
            out.append({"role": "tool", "content": msg.get("content", "")})

        else:
            out.append(msg)
    return out


class OllamaClient:
    def __init__(self, host: str, model: str, temperature: float = 0.1, max_tokens: int = 8192):
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    def check_connection(self) -> tuple[bool, str]:
        try:
            r = requests.get(self._url("/api/tags"), timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            return True, models
        except requests.ConnectionError:
            return False, f"Cannot connect to Ollama at {self.host}"
        except Exception as e:
            return False, str(e)

    def pull_model(self, model: str) -> Generator[str, None, None]:
        with requests.post(
            self._url("/api/pull"),
            json={"name": model},
            stream=True,
            timeout=600,
        ) as r:
            for line in r.iter_lines():
                if line:
                    data = json.loads(line)
                    yield data.get("status", "")

    def chat_stream(
        self,
        messages: list[dict],
        tools: bool = True,
    ) -> Generator[dict, None, None]:
        """
        Stream chat completions from Ollama.
        Yields dicts with keys:
          {"type": "text", "delta": str}
          {"type": "tool_call", "id": str, "name": str, "args": dict}
          {"type": "done", "usage": dict}
          {"type": "error", "message": str}
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + _sanitize_messages(messages),
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if tools:
            payload["tools"] = TOOL_SCHEMAS

        try:
            with requests.post(
                self._url("/api/chat"),
                json=payload,
                stream=True,
                timeout=120,
            ) as resp:
                resp.raise_for_status()

                accumulated_text = ""
                tool_calls_acc: list[dict] = []
                usage = {}

                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("error"):
                        yield {"type": "error", "message": chunk["error"]}
                        return

                    msg = chunk.get("message", {})
                    content = msg.get("content", "")
                    tool_calls = msg.get("tool_calls", [])

                    if content:
                        accumulated_text += content
                        yield {"type": "text", "delta": content}

                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        # Ollama may pass args as dict or JSON string
                        raw_args = fn.get("arguments", {})
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except Exception:
                                args = {}
                        else:
                            args = raw_args

                        call_id = tc.get("id") or f"call_{len(tool_calls_acc)}"
                        tool_calls_acc.append({"id": call_id, "name": name, "args": args})
                        yield {"type": "tool_call", "id": call_id, "name": name, "args": args}

                    if chunk.get("done"):
                        usage = {
                            "prompt_tokens": chunk.get("prompt_eval_count", 0),
                            "completion_tokens": chunk.get("eval_count", 0),
                        }
                        yield {"type": "done", "usage": usage, "text": accumulated_text, "tool_calls": tool_calls_acc}
                        return

        except requests.ConnectionError:
            yield {"type": "error", "message": f"Connection failed — is Ollama running at {self.host}?"}
        except requests.Timeout:
            yield {"type": "error", "message": "Request timed out (120s). Try a shorter prompt or check Ollama."}
        except requests.HTTPError as e:
            body = ""
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            yield {"type": "error", "message": f"HTTP {e.response.status_code}: {body}"}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
