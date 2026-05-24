"""
Tool implementations — file I/O, bash, search, git, npm dev, browser, keyboard,
self-compaction signal, and meta add_tool.
"""

import json
import os
import re
import subprocess
import shlex
import shutil
import threading
import fnmatch
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Avoid circular import — CONFIG_DIR is just a path
_CONFIG_DIR = Path.home() / ".alex-der"
DYNAMIC_TOOLS_FILE = _CONFIG_DIR / "dynamic_tools.py"

# ── Background process store ──────────────────────────────────────────────────
_bg_procs: dict[str, dict] = {}  # f"{cwd}:{script}" → {proc, logs, script, cwd}

# ── Dynamic tool registry ─────────────────────────────────────────────────────
_dynamic_fns: dict[str, Any] = {}
_dynamic_schemas: list[dict] = []

# ── Ollama tool schemas ───────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns file content with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "Start line (1-based)"},
                    "limit": {"type": "integer", "description": "Max lines"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact unique string in a file (surgical edit).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command. Returns stdout, stderr, returncode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds (default 30)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Default: ."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search for a pattern in files (ripgrep if available, else grep).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "case_insensitive": {"type": "boolean"},
                    "file_pattern": {"type": "string", "description": "e.g. '*.py'"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files by glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": "Run a git command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {"type": "string"},
                },
                "required": ["args"],
            },
        },
    },
    # ── New tools ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "npm_dev",
            "description": (
                "Manage a background npm dev/test/build server. "
                "action: start | stop | logs | status. "
                "Use script to override the npm script name (default: dev)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "logs", "status"],
                    },
                    "script": {"type": "string", "description": "npm script name (default: dev)"},
                    "port": {"type": "integer", "description": "Expected port to check"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse",
            "description": (
                "CLI web browser — fetch a URL and return readable text content. "
                "Uses w3m/lynx if installed, otherwise requests + HTML stripping."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "raw": {"type": "boolean", "description": "Return raw HTML instead of text"},
                    "links_only": {"type": "boolean", "description": "Return only hyperlinks"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keyboard",
            "description": (
                "Simulate keyboard input via xdotool. "
                "action: type | key | focus | screenshot. "
                "Requires xdotool (apt install xdotool)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["type", "key", "focus", "screenshot"],
                    },
                    "text": {"type": "string", "description": "Text to type (action=type)"},
                    "keys": {"type": "string", "description": "Key combo e.g. 'ctrl+c' (action=key)"},
                    "window": {"type": "string", "description": "Window name/id for focus or targeting"},
                    "delay_ms": {"type": "integer", "description": "Delay between keystrokes ms (default 0)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_tool",
            "description": (
                "Add a new tool to alex-der at runtime. "
                "The tool is immediately available and persisted to ~/.alex-der/dynamic_tools.py. "
                "python_body is the function body only (not the def line). "
                "The function receives cwd:str and **kwargs matching the parameters schema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Tool name (snake_case, no spaces)"},
                    "description": {"type": "string"},
                    "parameters_schema": {
                        "type": "string",
                        "description": "JSON string of the Ollama parameters schema object",
                    },
                    "python_body": {
                        "type": "string",
                        "description": "Python function body (indented 4 spaces). Must return a dict with ok:bool.",
                    },
                    "auto_approve": {
                        "type": "string",
                        "enum": ["reads", "writes", "bash"],
                        "description": "Auto-approve category (default: bash = manual)",
                    },
                },
                "required": ["name", "description", "parameters_schema", "python_body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Ask the user a question with selectable options. "
                "The user navigates with ↑/↓ arrow keys and confirms with Enter. "
                "For multi_select=true, Space toggles options and Enter confirms. "
                "Returns the user's selection. Use this to clarify ambiguous requests, "
                "ask preferences, or confirm before taking irreversible actions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of choices for the user",
                    },
                    "multi_select": {
                        "type": "boolean",
                        "description": "Allow selecting multiple options with Space (default: false)",
                    },
                    "allow_freetext": {
                        "type": "boolean",
                        "description": "Append a 'type custom answer' option at the end",
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compact_conversation",
            "description": (
                "Request the CLI to compact the conversation history into a summary. "
                "Call this when the context is growing very long and you want to free up space. "
                "The CLI will generate a summary and replace the history."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve(path: str, cwd: str) -> str:
    p = Path(path)
    return str(p.resolve() if p.is_absolute() else (Path(cwd) / p).resolve())


def _number_lines(text: str, offset: int = 1) -> str:
    lines = text.splitlines()
    width = len(str(offset + len(lines)))
    return "\n".join(f"{str(i + offset).rjust(width)}\t{line}" for i, line in enumerate(lines))

# ── Core tool implementations ─────────────────────────────────────────────────

def tool_read_file(path: str, cwd: str, offset: int = None, limit: int = None) -> dict:
    full = _resolve(path, cwd)
    try:
        with open(full, "r", errors="replace") as f:
            lines = f.readlines()
        start = (offset - 1) if offset and offset > 0 else 0
        end = (start + limit) if limit else len(lines)
        content = "".join(lines[start:end])
        return {"ok": True, "path": full, "content": _number_lines(content, start + 1), "total_lines": len(lines)}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {full}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_write_file(path: str, content: str, cwd: str) -> dict:
    full = _resolve(path, cwd)
    try:
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return {"ok": True, "path": full, "bytes_written": len(content.encode())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_edit_file(path: str, old_string: str, new_string: str, cwd: str) -> dict:
    full = _resolve(path, cwd)
    try:
        with open(full, "r", errors="replace") as f:
            original = f.read()
        count = original.count(old_string)
        if count == 0:
            return {"ok": False, "error": "old_string not found in file"}
        if count > 1:
            return {"ok": False, "error": f"old_string appears {count} times — be more specific"}
        with open(full, "w") as f:
            f.write(original.replace(old_string, new_string, 1))
        return {"ok": True, "path": full}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {full}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_bash(command: str, cwd: str, timeout: int = 30) -> dict:
    try:
        r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"ok": True, "stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_list_dir(path: str, cwd: str) -> dict:
    full = _resolve(path or ".", cwd)
    try:
        entries = sorted(os.listdir(full))
        result = []
        for e in entries:
            ep = os.path.join(full, e)
            result.append(f"{e}/" if os.path.isdir(ep) else f"{e}  ({os.path.getsize(ep)} bytes)")
        return {"ok": True, "path": full, "entries": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_grep(pattern: str, cwd: str, path: str = ".", case_insensitive: bool = False, file_pattern: str = None) -> dict:
    full_path = _resolve(path or ".", cwd)
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--line-number", "--no-heading", "--color=never"]
        if case_insensitive:
            cmd.append("-i")
        if file_pattern:
            cmd += ["-g", file_pattern]
        cmd += [pattern, full_path]
    else:
        cmd = ["grep", "-rn"] + (["-i"] if case_insensitive else [])
        if file_pattern:
            cmd += ["--include", file_pattern]
        cmd += [pattern, full_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = r.stdout.strip().splitlines()
        if len(lines) > 200:
            lines = lines[:200] + [f"... ({len(r.stdout.splitlines())} total)"]
        return {"ok": True, "matches": lines, "count": len(lines)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Search timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_find_files(pattern: str, cwd: str, path: str = ".") -> dict:
    full_path = _resolve(path or ".", cwd)
    matches = []
    _skip = {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build", ".next"}
    try:
        for root, dirs, files in os.walk(full_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _skip]
            for fname in files:
                if fnmatch.fnmatch(fname, pattern):
                    matches.append(os.path.relpath(os.path.join(root, fname), cwd))
        matches.sort()
        if len(matches) > 200:
            matches = matches[:200] + [f"... ({len(matches)} total)"]
        return {"ok": True, "matches": matches}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git(args: str, cwd: str) -> dict:
    try:
        r = subprocess.run(["git"] + shlex.split(args), cwd=cwd, capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── npm dev ───────────────────────────────────────────────────────────────────

def tool_npm_dev(action: str, cwd: str, script: str = "dev", port: int = None) -> dict:
    key = f"{cwd}:{script}"

    if action == "start":
        if key in _bg_procs and _bg_procs[key]["proc"].poll() is None:
            return {"ok": False, "error": f"Already running (PID {_bg_procs[key]['proc'].pid}). Stop it first."}
        if not shutil.which("npm"):
            return {"ok": False, "error": "npm not found — is Node.js installed?"}

        logs: list[str] = []

        try:
            proc = subprocess.Popen(
                ["npm", "run", script],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return {"ok": False, "error": "npm not found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        def _reader():
            for line in proc.stdout:
                logs.append(line.rstrip())
                if len(logs) > 500:
                    logs.pop(0)

        threading.Thread(target=_reader, daemon=True).start()
        _bg_procs[key] = {"proc": proc, "logs": logs, "script": script, "cwd": cwd}

        # Wait up to 5s for a "ready" signal
        import time
        _ready_patterns = re.compile(r"localhost|127\.0\.0\.1|ready|started|listening|compiled|dev server", re.I)
        for _ in range(25):
            time.sleep(0.2)
            if proc.poll() is not None:
                return {"ok": False, "error": f"Process exited early (rc={proc.returncode})", "logs": logs[-20:]}
            if any(_ready_patterns.search(ln) for ln in logs[-10:]):
                break

        if port:
            url = f"http://localhost:{port}"
        else:
            # Extract port from logs
            url_match = re.search(r"https?://localhost:?(\d+)", "\n".join(logs))
            url = url_match.group(0) if url_match else None

        return {
            "ok": True,
            "action": "started",
            "pid": proc.pid,
            "script": script,
            "url": url,
            "initial_logs": logs[-30:],
        }

    elif action == "stop":
        if key not in _bg_procs:
            return {"ok": False, "error": "No running process found for this cwd/script"}
        entry = _bg_procs.pop(key)
        proc = entry["proc"]
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return {"ok": True, "action": "stopped", "pid": proc.pid}

    elif action == "logs":
        if key not in _bg_procs:
            return {"ok": False, "error": "No running process found"}
        entry = _bg_procs[key]
        return {
            "ok": True,
            "running": entry["proc"].poll() is None,
            "pid": entry["proc"].pid,
            "logs": entry["logs"][-80:],
        }

    elif action == "status":
        if key not in _bg_procs:
            return {"ok": True, "running": False}
        entry = _bg_procs[key]
        running = entry["proc"].poll() is None
        return {"ok": True, "running": running, "pid": entry["proc"].pid, "script": entry["script"]}

    return {"ok": False, "error": f"Unknown action '{action}'. Use start/stop/logs/status"}

# ── CLI browser ───────────────────────────────────────────────────────────────

class _HtmlToText(HTMLParser):
    """Minimal but effective HTML → text converter."""

    _BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "li", "tr", "article", "section", "header", "footer", "pre"}
    _SKIP = {"script", "style", "noscript", "svg", "iframe", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title: str = ""
        self._skip_depth = 0
        self._in_title = False
        self._cur_href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a":
            d = dict(attrs)
            self._cur_href = d.get("href", "")
        if tag == "img":
            d = dict(attrs)
            alt = d.get("alt", "")
            if alt:
                self.parts.append(f"[img: {alt}] ")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._cur_href:
            self.links.append(self._cur_href)
            self._cur_href = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = text
        else:
            self.parts.append(text + " ")

    def result(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r" {2,}", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def tool_browse(url: str, cwd: str, raw: bool = False, links_only: bool = False) -> dict:
    import html as _html

    # Try external text browsers first
    for browser in ("w3m", "lynx", "elinks"):
        if shutil.which(browser):
            flags = {
                "w3m": ["-dump", "-T", "text/html"],
                "lynx": ["-dump", "-nolist"],
                "elinks": ["-dump"],
            }[browser]
            try:
                r = subprocess.run([browser] + flags + [url], capture_output=True, text=True, timeout=20)
                if r.returncode == 0 and r.stdout.strip():
                    return {
                        "ok": True,
                        "url": url,
                        "renderer": browser,
                        "content": r.stdout[:8000],
                        "truncated": len(r.stdout) > 8000,
                    }
            except Exception:
                pass

    # Fallback: requests + HTML parser
    try:
        import requests as _req
        headers = {"User-Agent": "alex-der/2.0 (CLI browser; +https://github.com/alex-der)"}
        resp = _req.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()

        ctype = resp.headers.get("content-type", "")

        if raw:
            return {"ok": True, "url": resp.url, "status": resp.status_code, "content": resp.text[:6000]}

        if "text/html" not in ctype and "text/plain" not in ctype:
            return {"ok": True, "url": resp.url, "status": resp.status_code, "content_type": ctype, "size": len(resp.content)}

        if "text/plain" in ctype:
            return {"ok": True, "url": resp.url, "status": resp.status_code, "content": resp.text[:8000]}

        parser = _HtmlToText()
        parser.feed(_html.unescape(resp.text))
        text = parser.result()

        if links_only:
            return {"ok": True, "url": resp.url, "links": parser.links[:100]}

        return {
            "ok": True,
            "url": resp.url,
            "status": resp.status_code,
            "title": parser.title,
            "content": text[:8000],
            "links": parser.links[:30],
            "truncated": len(text) > 8000,
            "renderer": "requests+htmlparser",
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Keyboard (xdotool) ────────────────────────────────────────────────────────

def tool_keyboard(action: str, cwd: str, text: str = None, keys: str = None,
                  window: str = None, delay_ms: int = 0) -> dict:
    # Wayland: try ydotool; X11: xdotool
    for tool in ("xdotool", "ydotool"):
        if shutil.which(tool):
            _kbtool = tool
            break
    else:
        return {
            "ok": False,
            "error": "Neither xdotool nor ydotool found.\n"
                     "Install: sudo apt install xdotool  (X11)\n"
                     "      or: sudo apt install ydotool (Wayland)",
        }

    def _run(*cmd_args):
        try:
            r = subprocess.run(list(cmd_args), capture_output=True, text=True, timeout=10)
            return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Keyboard command timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if action == "type":
        if not text:
            return {"ok": False, "error": "text is required for action='type'"}
        if _kbtool == "xdotool":
            cmd = [_kbtool, "type", "--delay", str(delay_ms)]
            if window:
                cmd += ["--window", window]
            cmd += ["--", text]
        else:
            cmd = [_kbtool, "type", text]
        return _run(*cmd)

    elif action == "key":
        if not keys:
            return {"ok": False, "error": "keys is required for action='key'"}
        if _kbtool == "xdotool":
            cmd = [_kbtool, "key"]
            if window:
                cmd += ["--window", window]
            cmd += keys.split()
        else:
            cmd = [_kbtool, "key", keys]
        return _run(*cmd)

    elif action == "focus":
        if not window:
            return {"ok": False, "error": "window is required for action='focus'"}
        if _kbtool == "xdotool":
            r = subprocess.run(
                [_kbtool, "search", "--name", window],
                capture_output=True, text=True, timeout=5,
            )
            wid = r.stdout.strip().splitlines()[0] if r.stdout.strip() else None
            if not wid:
                return {"ok": False, "error": f"Window '{window}' not found"}
            return _run(_kbtool, "windowfocus", wid)
        return {"ok": False, "error": "focus not supported with ydotool"}

    elif action == "screenshot":
        out = f"/tmp/alex-der-shot-{os.getpid()}.png"
        for scrot in ("scrot", "gnome-screenshot", "spectacle"):
            if not shutil.which(scrot):
                continue
            flags = {"scrot": [out], "gnome-screenshot": ["-f", out], "spectacle": ["-o", out, "-b"]}[scrot]
            r = _run(scrot, *flags)
            if r.get("ok"):
                return {"ok": True, "path": out, "tool": scrot}
        return {"ok": False, "error": "No screenshot tool found (scrot, gnome-screenshot, spectacle)"}

    return {"ok": False, "error": f"Unknown action '{action}'. Use type/key/focus/screenshot"}

# ── add_tool (meta) ───────────────────────────────────────────────────────────

def tool_add_tool(name: str, description: str, parameters_schema: str, python_body: str,
                  cwd: str, auto_approve: str = "bash") -> dict:
    # Validate name
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        return {"ok": False, "error": "name must be lowercase snake_case, start with a letter"}

    existing_names = {s["function"]["name"] for s in TOOL_SCHEMAS} | set(_dynamic_fns)
    if name in existing_names:
        return {"ok": False, "error": f"Tool '{name}' already exists"}

    # Parse schema
    if isinstance(parameters_schema, str):
        try:
            schema_obj = json.loads(parameters_schema)
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Invalid parameters_schema JSON: {e}"}
    else:
        schema_obj = parameters_schema

    # Build and compile function
    fn_lines = [f"def tool_{name}(cwd='.', **kwargs):"]
    for line in python_body.splitlines():
        fn_lines.append(f"    {line}")
    fn_src = "\n".join(fn_lines)

    try:
        code_obj = compile(fn_src, f"<dynamic:{name}>", "exec")
    except SyntaxError as e:
        return {"ok": False, "error": f"Syntax error: {e}"}

    ns: dict[str, Any] = {
        "os": os, "subprocess": subprocess, "shlex": shlex, "shutil": shutil,
        "json": json, "Path": Path, "re": re,
    }
    try:
        exec(code_obj, ns)
        fn = ns[f"tool_{name}"]
    except Exception as e:
        return {"ok": False, "error": f"Error executing tool code: {e}"}

    # Build schema entry
    schema_entry = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema_obj,
        },
    }
    auto_flag = f"auto_approve_{auto_approve}"

    # Register in memory
    _dynamic_fns[name] = fn
    _dynamic_schemas.append(schema_entry)
    TOOL_SCHEMAS.append(schema_entry)
    AUTO_APPROVE_MAP[name] = auto_flag
    TOOL_DESCRIPTIONS[name] = (name, "magenta")

    # Persist
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    header = "# alex-der dynamic tools — auto-generated\nimport os, subprocess, shlex, json, re\nfrom pathlib import Path\n\n"
    existing = ""
    if DYNAMIC_TOOLS_FILE.exists():
        with open(DYNAMIC_TOOLS_FILE) as f:
            existing = f.read()
        if not existing.startswith("#"):
            existing = header
    else:
        existing = header

    entry_block = (
        f"\n# ── {name} ──\n"
        f"{fn_src}\n"
        f"_DYNAMIC_REGISTRY.append({{\n"
        f"    'name': {json.dumps(name)},\n"
        f"    'description': {json.dumps(description)},\n"
        f"    'schema': {json.dumps(schema_entry)},\n"
        f"    'auto_approve': {json.dumps(auto_flag)},\n"
        f"}})\n"
    )

    # Ensure _DYNAMIC_REGISTRY exists in file
    if "_DYNAMIC_REGISTRY" not in existing:
        existing += "\n_DYNAMIC_REGISTRY = []\n"

    with open(DYNAMIC_TOOLS_FILE, "w") as f:
        f.write(existing + entry_block)

    return {
        "ok": True,
        "name": name,
        "message": f"Tool '{name}' added and persisted to {DYNAMIC_TOOLS_FILE}",
    }


def _load_dynamic_tools():
    """Load persisted dynamic tools from ~/.alex-der/dynamic_tools.py."""
    if not DYNAMIC_TOOLS_FILE.exists():
        return

    ns: dict[str, Any] = {
        "_DYNAMIC_REGISTRY": [],
        "os": os, "subprocess": subprocess, "shlex": shlex, "shutil": shutil,
        "json": json, "Path": Path, "re": re,
    }
    try:
        with open(DYNAMIC_TOOLS_FILE) as f:
            src = f.read()
        exec(compile(src, str(DYNAMIC_TOOLS_FILE), "exec"), ns)

        for entry in ns.get("_DYNAMIC_REGISTRY", []):
            n = entry["name"]
            fn = ns.get(f"tool_{n}")
            if not fn:
                continue
            _dynamic_fns[n] = fn
            _dynamic_schemas.append(entry["schema"])
            TOOL_SCHEMAS.append(entry["schema"])
            AUTO_APPROVE_MAP[n] = entry.get("auto_approve", "auto_approve_bash")
            TOOL_DESCRIPTIONS[n] = (n, "magenta")
    except Exception as e:
        import sys
        print(f"[alex-der] Warning: failed to load dynamic tools: {e}", file=sys.stderr)

# ── Dispatcher ────────────────────────────────────────────────────────────────

def dispatch(name: str, args: dict, cwd: str) -> dict:
    if name == "read_file":
        return tool_read_file(args["path"], cwd, args.get("offset"), args.get("limit"))
    elif name == "write_file":
        return tool_write_file(args["path"], args["content"], cwd)
    elif name == "edit_file":
        return tool_edit_file(args["path"], args["old_string"], args["new_string"], cwd)
    elif name == "bash":
        return tool_bash(args["command"], cwd, args.get("timeout", 30))
    elif name == "list_dir":
        return tool_list_dir(args.get("path", "."), cwd)
    elif name == "grep":
        return tool_grep(args["pattern"], cwd, args.get("path", "."),
                         args.get("case_insensitive", False), args.get("file_pattern"))
    elif name == "find_files":
        return tool_find_files(args["pattern"], cwd, args.get("path", "."))
    elif name == "git":
        return tool_git(args["args"], cwd)
    elif name == "npm_dev":
        return tool_npm_dev(args["action"], cwd, args.get("script", "dev"), args.get("port"))
    elif name == "browse":
        return tool_browse(args["url"], cwd, args.get("raw", False), args.get("links_only", False))
    elif name == "keyboard":
        return tool_keyboard(args["action"], cwd, args.get("text"), args.get("keys"),
                             args.get("window"), args.get("delay_ms", 0))
    elif name == "add_tool":
        return tool_add_tool(
            args["name"], args["description"], args["parameters_schema"],
            args["python_body"], cwd, args.get("auto_approve", "bash"),
        )
    elif name == "compact_conversation":
        # Handled specially in cli.py — should not reach here
        return {"ok": True, "signal": "compact"}
    elif name == "ask_user":
        # Handled specially in cli.py — should not reach here
        return {"ok": False, "error": "ask_user must be handled by the CLI (no terminal access here)"}
    elif name in _dynamic_fns:
        try:
            return _dynamic_fns[name](cwd=cwd, **args)
        except Exception as e:
            return {"ok": False, "error": f"Dynamic tool error: {e}"}
    else:
        return {"ok": False, "error": f"Unknown tool: {name}"}


# ── UI metadata ───────────────────────────────────────────────────────────────

TOOL_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "read_file":             ("Read",     "cyan"),
    "write_file":            ("Write",    "yellow"),
    "edit_file":             ("Edit",     "yellow"),
    "bash":                  ("Bash",     "red"),
    "list_dir":              ("List",     "cyan"),
    "grep":                  ("Search",   "green"),
    "find_files":            ("Find",     "green"),
    "git":                   ("Git",      "magenta"),
    "npm_dev":               ("npm dev",  "bright_green"),
    "browse":                ("Browse",   "blue"),
    "keyboard":              ("Keyboard", "bright_yellow"),
    "add_tool":              ("AddTool",  "bright_magenta"),
    "compact_conversation":  ("Compact",  "dim"),
    "ask_user":              ("AskUser",  "bright_cyan"),
}

AUTO_APPROVE_MAP: dict[str, str] = {
    "read_file":            "auto_approve_reads",
    "list_dir":             "auto_approve_reads",
    "grep":                 "auto_approve_reads",
    "find_files":           "auto_approve_reads",
    "write_file":           "auto_approve_writes",
    "edit_file":            "auto_approve_writes",
    "bash":                 "auto_approve_bash",
    "git":                  "auto_approve_bash",
    "npm_dev":              "auto_approve_bash",
    "browse":               "auto_approve_reads",
    "keyboard":             "auto_approve_bash",
    "add_tool":             "auto_approve_bash",
    "compact_conversation": "auto_approve_bash",
    "ask_user":             "auto_approve_reads",  # always allowed — user is answering, not approving
}
