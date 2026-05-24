"""
Conversation state and session persistence.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SESSIONS_DIR


class Conversation:
    def __init__(self, session_id: str = None, cwd: str = "."):
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.cwd = cwd
        self.messages: list[dict] = []
        self.created_at = datetime.now().isoformat()
        self._path = SESSIONS_DIR / f"{self.session_id}.json"

    # ── Message helpers ───────────────────────────────────────────────────────

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str, tool_calls: list = None):
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, name: str, result: dict):
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": json.dumps(result),
        })

    def get_messages(self) -> list[dict]:
        return self.messages

    def last_assistant(self) -> str | None:
        for msg in reversed(self.messages):
            if msg["role"] == "assistant":
                return msg.get("content", "")
        return None

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self):
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump({
                "session_id": self.session_id,
                "cwd": self.cwd,
                "created_at": self.created_at,
                "messages": self.messages,
            }, f, indent=2)

    @classmethod
    def load(cls, session_id: str) -> "Conversation":
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        with open(path) as f:
            data = json.load(f)
        conv = cls(session_id=data["session_id"], cwd=data["cwd"])
        conv.created_at = data.get("created_at", conv.created_at)
        conv.messages = data["messages"]
        return conv

    @classmethod
    def list_sessions(cls) -> list[dict]:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        sessions = []
        for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(p) as f:
                    data = json.load(f)
                user_msgs = [m for m in data.get("messages", []) if m["role"] == "user"]
                preview = user_msgs[0]["content"][:60] if user_msgs else "(empty)"
                sessions.append({
                    "id": data["session_id"],
                    "cwd": data.get("cwd", "?"),
                    "created_at": data.get("created_at", "?"),
                    "message_count": len(data.get("messages", [])),
                    "preview": preview,
                })
            except Exception:
                pass
        return sessions
