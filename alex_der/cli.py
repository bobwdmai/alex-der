"""
Main TUI/CLI — Rich-powered interactive loop.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .ai import OllamaClient
from .config import Config
from .conversation import Conversation
from .loader import LoadingAnimation, tool_phrase
from .selector import ask as selector_ask
from .tools import (
    AUTO_APPROVE_MAP, TOOL_DESCRIPTIONS, TOOL_SCHEMAS,
    dispatch, _load_dynamic_tools,
)

console = Console()

BANNER = """\
[bold cyan] ██████╗  ██████╗ ██████╗       ██████╗ ███████╗██████╗ [/]
[bold cyan]██╔══██╗██╔═══██╗██╔══██╗      ██╔══██╗██╔════╝██╔══██╗[/]
[bold cyan]██████╔╝██║   ██║██████╔╝█████╗██║  ██║█████╗  ██████╔╝[/]
[bold cyan]██╔══██╗██║   ██║██╔══██╗╚════╝██║  ██║██╔══╝  ██╔══██╗[/]
[bold cyan]██████╔╝╚██████╔╝██████╔╝      ██████╔╝███████╗██║  ██║[/]
[bold cyan]╚═════╝  ╚═════╝ ╚═════╝       ╚═════╝ ╚══════╝╚═╝  ╚═╝[/]
[dim]  bob-der2.0 · codename [bold]alex-der[/bold] · powered by Ollama[/dim]
"""

HELP_TEXT = """
[bold]Commands[/bold]
  [cyan]/help[/cyan]              This message
  [cyan]/model[/cyan] [name]      Switch model (current session)
  [cyan]/cwd[/cyan] [path]        Change working directory
  [cyan]/compact[/cyan]           Summarize + compress conversation history
  [cyan]/sessions[/cyan]          List saved sessions
  [cyan]/load[/cyan] [id]         Load a saved session
  [cyan]/new[/cyan]               Start a fresh session
  [cyan]/clear[/cyan]             Clear conversation history
  [cyan]/config[/cyan]            Show current config
  [cyan]/set[/cyan] key value     Set a config value
  [cyan]/approve[/cyan]           Show auto-approve flags
  [cyan]/tools[/cyan]             List available tools (incl. dynamic)
  [cyan]/ask[/cyan] q opt1,opt2  Ask yourself a quick question interactively
  [cyan]/npm[/cyan] [script]      Quick-start npm dev server (default: dev)
  [cyan]/browse[/cyan] [url]      Fetch a URL in the CLI browser
  [cyan]/shortcut[/cyan]          Install alex-der & bob-der2 to ~/.local/bin
  [cyan]/status[/cyan]            Show Ollama + model status
  [cyan]/exit[/cyan], [cyan]/quit[/cyan], Ctrl+D   Exit

[bold]Input[/bold]
  Multi-line: end a line with \\ to continue
  Empty input: skip (no API call)
"""


def _tool_label(name: str) -> tuple[str, str]:
    return TOOL_DESCRIPTIONS.get(name, (name, "white"))


def _render_tool_call(name: str, args: dict) -> Panel:
    label, color = _tool_label(name)
    lines = []
    for k, v in args.items():
        val = str(v)
        if len(val) > 120:
            val = val[:117] + "..."
        lines.append(f"  [dim]{k}:[/dim] {val}")
    body = "\n".join(lines) if lines else "  [dim](no args)[/dim]"
    return Panel(body, title=f"[{color}]⚙ {label}[/{color}]", border_style=color, expand=False)


def _render_tool_result(name: str, result: dict) -> str:
    ok = result.get("ok", True)
    color = "green" if ok else "red"
    icon = "✓" if ok else "✗"

    if not ok:
        return f"[{color}]{icon} {result.get('error', 'error')}[/{color}]"

    if name == "read_file":
        content = result.get("content", "")
        path = result.get("path", "")
        ext = Path(path).suffix.lstrip(".")
        lines_shown = len(content.splitlines())
        total = result.get("total_lines", lines_shown)
        suffix = f" [dim]({lines_shown}/{total} lines)[/dim]" if lines_shown < total else ""
        console.print(Syntax(content, ext or "text", theme="monokai", line_numbers=False), overflow="fold")
        return f"[{color}]{icon} Read {path}{suffix}[/{color}]"

    if name in ("write_file", "edit_file"):
        return f"[{color}]{icon} {result.get('path', '')}[/{color}]"

    if name == "bash":
        stdout = result.get("stdout", "").strip()
        stderr = result.get("stderr", "").strip()
        rc = result.get("returncode", 0)
        c = "green" if rc == 0 else "red"
        if stdout:
            console.print(Syntax(stdout[:3000], "bash", theme="monokai"))
        if stderr:
            console.print(f"[yellow]{stderr[:1000]}[/yellow]")
        return f"[{c}]{icon} exit {rc}[/{c}]"

    if name == "list_dir":
        entries = result.get("entries", [])
        t = Table(show_header=False, box=None, padding=(0, 1))
        for e in entries[:50]:
            t.add_row(f"[bold blue]{e}[/bold blue]" if e.endswith("/") else f"[white]{e}[/white]")
        console.print(t)
        return f"[{color}]{icon} {len(entries)} entries[/{color}]"

    if name in ("grep", "find_files"):
        matches = result.get("matches", [])
        for m in matches[:50]:
            console.print(f"  [dim]{m}[/dim]")
        return f"[{color}]{icon} {result.get('count', len(matches))} matches[/{color}]"

    if name == "git":
        stdout = result.get("stdout", "").strip()
        stderr = result.get("stderr", "").strip()
        if stdout:
            console.print(f"[dim]{stdout[:2000]}[/dim]")
        if stderr and not ok:
            console.print(f"[red]{stderr[:500]}[/red]")
        return f"[{color}]{icon} git rc={result.get('returncode', '?')}[/{color}]"

    if name == "npm_dev":
        action = result.get("action", "")
        if action == "started":
            url = result.get("url") or ""
            url_str = f" → [link]{url}[/link]" if url else ""
            logs = result.get("initial_logs", [])
            for ln in logs[-10:]:
                console.print(f"  [dim]{ln}[/dim]")
            return f"[{color}]{icon} npm dev started (PID {result.get('pid')}){url_str}[/{color}]"
        elif action == "stopped":
            return f"[{color}]{icon} npm dev stopped (PID {result.get('pid')})[/{color}]"
        else:
            running = result.get("running", False)
            logs = result.get("logs", [])
            for ln in logs[-15:]:
                console.print(f"  [dim]{ln}[/dim]")
            return f"[{color}]{icon} running={'yes' if running else 'no'}[/{color}]"

    if name == "browse":
        title = result.get("title", "")
        url = result.get("url", "")
        renderer = result.get("renderer", "")
        content = result.get("content", "")
        links = result.get("links", [])
        if content:
            console.print(Panel(content[:4000], title=f"[blue]{title or url}[/blue]", border_style="blue"))
        if links:
            console.print(f"  [dim]Links: {', '.join(links[:5])}{'...' if len(links) > 5 else ''}[/dim]")
        trunc = " [dim](truncated)[/dim]" if result.get("truncated") else ""
        return f"[{color}]{icon} {url} [{renderer}]{trunc}[/{color}]"

    if name == "keyboard":
        action = result.get("action", "")
        out = result.get("stdout", "") or result.get("path", "")
        return f"[{color}]{icon} keyboard {action}{(' → ' + out) if out else ''}[/{color}]"

    if name == "add_tool":
        msg = result.get("message", "")
        return f"[{color}]{icon} {msg}[/{color}]"

    if name == "compact_conversation":
        return f"[{color}]{icon} compacted[/{color}]"

    if name == "ask_user":
        if result.get("cancelled"):
            return f"[yellow]  cancelled[/yellow]"
        sel = result.get("selection")
        disp = ", ".join(sel) if isinstance(sel, list) else str(sel)
        return f"[{color}]{icon} selected: [bold]{disp}[/bold][/{color}]"

    return f"[{color}]{icon}[/{color}] {json.dumps(result)[:200]}"


def _ask_permission(name: str, args: dict) -> bool:
    console.print(_render_tool_call(name, args))
    return Confirm.ask("  [bold]Allow?[/bold]", default=True)


def _multi_line_input(prompt_str: str) -> str:
    lines = []
    first = True
    while True:
        try:
            line = Prompt.ask(prompt_str) if first else input("... ")
            first = False
        except (EOFError, KeyboardInterrupt):
            return ""
        if line.endswith("\\"):
            lines.append(line[:-1])
        else:
            lines.append(line)
            break
    return "\n".join(lines).strip()

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_sessions():
    sessions = Conversation.list_sessions()
    if not sessions:
        console.print("[dim]No saved sessions.[/dim]")
        return
    t = Table("ID", "Created", "Msgs", "Preview", box=None)
    for s in sessions[:20]:
        t.add_row(s["id"], s["created_at"][:16], str(s["message_count"]), s["preview"])
    console.print(t)


def cmd_tools():
    t = Table("Tool", "Category", "Description", box=None)
    cats = {
        "read_file": ("I/O", "Read file with line numbers"),
        "write_file": ("I/O", "Create or overwrite a file"),
        "edit_file": ("I/O", "Surgical string replacement"),
        "bash": ("Shell", "Execute shell commands"),
        "list_dir": ("Search", "List directory contents"),
        "grep": ("Search", "Regex search in files"),
        "find_files": ("Search", "Find files by glob"),
        "git": ("VCS", "Run git commands"),
        "npm_dev": ("Dev", "Start/stop/log npm dev server"),
        "browse": ("Web", "CLI web browser"),
        "keyboard": ("Input", "Simulate keyboard via xdotool"),
        "add_tool": ("Meta", "Add a new tool at runtime"),
        "compact_conversation": ("Meta", "Compress conversation history"),
        "ask_user":             ("Meta", "Ask user a question with arrow-key picker"),
    }
    for schema in TOOL_SCHEMAS:
        n = schema["function"]["name"]
        label, color = _tool_label(n)
        cat, desc = cats.get(n, ("Dynamic", schema["function"].get("description", "")[:50]))
        t.add_row(f"[{color}]{n}[/{color}]", cat, desc)
    console.print(t)
    if any(n not in cats for n in (s["function"]["name"] for s in TOOL_SCHEMAS)):
        console.print(f"[dim]* Dynamic tools loaded from ~/.alex-der/dynamic_tools.py[/dim]")


def cmd_config(cfg: Config):
    import dataclasses
    t = Table("Key", "Value", box=None)
    for f in dataclasses.fields(cfg):
        t.add_row(f"[cyan]{f.name}[/cyan]", str(getattr(cfg, f.name)))
    console.print(t)


def cmd_approve(cfg: Config):
    for flag in ("auto_approve_reads", "auto_approve_writes", "auto_approve_bash"):
        val = getattr(cfg, flag)
        console.print(f"  {flag}: [bold]{'on' if val else 'off'}[/bold]")
    console.print()
    console.print("  Use [cyan]/set auto_approve_reads true[/cyan] etc. to change.")


def cmd_status(client: OllamaClient):
    ok, info = client.check_connection()
    if ok:
        console.print(f"[green]✓ Ollama online[/green] at [dim]{client.host}[/dim]")
        if isinstance(info, list):
            console.print(f"  Models: {', '.join(info[:10]) or '(none)'}")
        avail = isinstance(info, list) and client.model in info
        icon = "[green]✓[/green]" if avail else "[yellow]![/yellow]"
        note = "" if avail else " (not pulled — will pull on first use)"
        console.print(f"  {icon} Model [bold]{client.model}[/bold]{note}")
    else:
        console.print(f"[red]✗ Ollama offline:[/red] {info}")


def cmd_compact(conv: Conversation, client: OllamaClient) -> int:
    if not conv.messages:
        console.print("[dim]Nothing to compact.[/dim]")
        return 0

    before = len(conv.messages)
    console.print(f"[dim]Compacting {before} messages...[/dim]")

    summary_msgs = list(conv.messages) + [{
        "role": "user",
        "content": (
            "Produce a dense technical summary of this conversation. "
            "Include: all files read/modified and key changes, decisions made, "
            "current state of any work in progress, important context, commands run and their results. "
            "This summary replaces the full history — be complete but concise. Use markdown."
        ),
    }]

    summary = ""
    for event in client.chat_stream(summary_msgs, tools=False):
        if event["type"] == "text":
            summary += event["delta"]
        elif event["type"] in ("done", "error"):
            break

    if not summary.strip():
        console.print("[red]Got empty summary — not compacting.[/red]")
        return before

    conv.messages = [
        {"role": "user", "content": f"[Compacted conversation summary]\n\n{summary}"},
        {"role": "assistant", "content": "Got it — I have full context from the summary. What's next?"},
    ]
    conv.save()
    after = len(conv.messages)
    console.print(f"[green]Compacted[/green] {before} → {after} messages")
    console.print(Markdown(summary[:1000] + ("..." if len(summary) > 1000 else "")))
    return after


def cmd_shortcut(main_py: str):
    """Install alex-der and bob-der2 wrapper scripts to ~/.local/bin."""
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    script = f'#!/usr/bin/env bash\nexec python3 "{main_py}" "$@"\n'

    for sname in ("alex-der", "bob-der2"):
        target = bin_dir / sname
        target.write_text(script)
        target.chmod(0o755)
        console.print(f"[green]Created[/green] {target}")

    path_dirs = os.environ.get("PATH", "").split(":")
    if str(bin_dir) not in path_dirs:
        console.print(f"\n[yellow]Note:[/yellow] {bin_dir} is not in PATH. Add to ~/.bashrc:")
        console.print(f'  [dim]export PATH="$HOME/.local/bin:$PATH"[/dim]')
    else:
        console.print(f"\n[green]Ready![/green] Run [bold]alex-der[/bold] or [bold]bob-der2[/bold] from anywhere.")


def cmd_npm_quick(script: str, cfg: Config):
    """Quick npm run <script> — start and tail logs."""
    from .tools import tool_npm_dev
    console.print(f"[dim]Starting npm run {script} in {cfg.working_dir}...[/dim]")
    result = tool_npm_dev("start", cfg.working_dir, script)
    if result.get("ok"):
        url = result.get("url") or ""
        url_str = f" — [link]{url}[/link]" if url else ""
        console.print(f"[green]✓ Started[/green] (PID {result.get('pid')}){url_str}")
        for ln in result.get("initial_logs", [])[-15:]:
            console.print(f"  [dim]{ln}[/dim]")
    else:
        console.print(f"[red]✗[/red] {result.get('error')}")


def cmd_browse_quick(url: str, cfg: Config):
    """Quick browser fetch and display."""
    from .tools import tool_browse
    with Live(LoadingAnimation("fetching"), console=console,
              refresh_per_second=12, transient=True):
        result = tool_browse(url, cfg.working_dir)
    _render_tool_result("browse", result)

# ── Agent loop ────────────────────────────────────────────────────────────────

def run_agent_turn(client: OllamaClient, conv: Conversation, cfg: Config) -> bool:
    MAX_ROUNDS = 20

    for _ in range(MAX_ROUNDS):
        assistant_text = ""
        pending_tool_calls: list[dict] = []
        error_msg = None

        console.print()
        console.print(Rule(style="dim"))
        console.print("[bold cyan]alex-der[/bold cyan] ", end="")

        _anim = LoadingAnimation()
        with Live(_anim, console=console, refresh_per_second=15, transient=False) as live:
            live_text = Text()
            _streaming = False
            for event in client.chat_stream(conv.get_messages()):
                etype = event["type"]
                if etype == "text":
                    if not _streaming:
                        _streaming = True
                        live.update(live_text)   # swap animation out for text
                    assistant_text += event["delta"]
                    live_text.append(event["delta"])
                    live.update(live_text)
                elif etype == "tool_call":
                    pending_tool_calls.append(event)
                elif etype == "done":
                    break
                elif etype == "error":
                    error_msg = event["message"]
                    break

        if error_msg:
            console.print(f"\n[red]Error:[/red] {error_msg}")
            return False

        if assistant_text.strip():
            console.print()
            console.print(Markdown(assistant_text))

        tool_call_records = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
            for tc in pending_tool_calls
        ]
        conv.add_assistant(assistant_text, tool_call_records or None)

        if not pending_tool_calls:
            conv.save()
            return True

        all_denied = True
        for tc in pending_tool_calls:
            name, args, call_id = tc["name"], tc["args"], tc["id"]
            console.print()

            # compact_conversation is handled here, not dispatched
            if name == "compact_conversation":
                console.print("[dim]  AI requested compaction[/dim]")
                cmd_compact(conv, client)
                conv.add_tool_result(call_id, name, {"ok": True, "message": "Conversation compacted."})
                all_denied = False
                continue

            # ask_user runs the interactive selector directly in the terminal
            if name == "ask_user":
                question = args.get("question", "Choose an option:")
                options  = args.get("options", [])
                multi    = args.get("multi_select", False)
                freetext = args.get("allow_freetext", False)

                if not options:
                    conv.add_tool_result(call_id, name, {"ok": False, "error": "No options provided."})
                    all_denied = False
                    continue

                console.print(_render_tool_call(name, {"question": question, "options": options}))
                # Flush Rich output before switching terminal to raw mode
                console.file.flush()

                result = selector_ask(question, options, multi=multi, allow_freetext=freetext)

                summary = _render_tool_result(name, result)
                console.print(f"  {summary}")
                conv.add_tool_result(call_id, name, result)
                all_denied = False
                continue

            auto_flag = AUTO_APPROVE_MAP.get(name, "auto_approve_bash")
            auto_approved = getattr(cfg, auto_flag, False)

            if auto_approved:
                console.print(_render_tool_call(name, args))
                approved = True
            else:
                approved = _ask_permission(name, args)

            if not approved:
                conv.add_tool_result(call_id, name, {"ok": False, "error": "User denied."})
                console.print("[dim]  denied[/dim]")
                continue

            all_denied = False

            with Live(LoadingAnimation(f"running {name}"), console=console,
                      refresh_per_second=12, transient=True):
                result = dispatch(name, args, cfg.working_dir)

            summary = _render_tool_result(name, result)
            console.print(f"  {summary}")
            conv.add_tool_result(call_id, name, result)

        conv.save()
        if all_denied:
            return True

    console.print("[yellow]Warning:[/yellow] reached max tool rounds (20)")
    return True

# ── Main entry ────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="alex-der",
        description="bob-der2.0 · AI coding assistant (codename: alex-der)",
    )
    parser.add_argument("--model", "-m", help="Override Ollama model")
    parser.add_argument("--host", help="Ollama host URL")
    parser.add_argument("--cwd", "-C", help="Working directory")
    parser.add_argument("--session", "-s", help="Resume session by ID")
    parser.add_argument("--no-banner", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    from alex_der import __version__, __codename__

    if args.version:
        print(f"bob-der2.0 ({__codename__}) v{__version__}")
        sys.exit(0)

    # Load dynamic tools before starting
    _load_dynamic_tools()

    cfg = Config.load()
    if args.model:
        cfg.model = args.model
    if args.host:
        cfg.ollama_host = args.host
    cfg.working_dir = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()

    # Resolve main.py path for /shortcut
    _main_py = str(Path(__file__).parent.parent / "main.py")

    client = OllamaClient(
        host=cfg.ollama_host,
        model=cfg.model,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )

    if not args.no_banner:
        console.print(BANNER)

    ok, info = client.check_connection()
    if not ok:
        console.print(f"[red]⚠  Ollama not reachable at {cfg.ollama_host}[/red]")
        console.print(f"   {info}")
        console.print("[dim]   Start Ollama with: ollama serve[/dim]\n")
    else:
        console.print(f"[dim]Ollama online · model: [bold]{cfg.model}[/bold] · cwd: {cfg.working_dir}[/dim]")

    if args.session:
        try:
            conv = Conversation.load(args.session)
            console.print(f"[green]Resumed[/green] [cyan]{conv.session_id}[/cyan] ({len(conv.messages)} msgs)")
        except FileNotFoundError:
            console.print(f"[red]Session not found:[/red] {args.session}")
            conv = Conversation(cwd=cfg.working_dir)
    else:
        conv = Conversation(cwd=cfg.working_dir)

    console.print(f"[dim]Session [cyan]{conv.session_id}[/cyan] · /help for commands[/dim]\n")

    # ── REPL ─────────────────────────────────────────────────────────────────
    while True:
        try:
            user_input = _multi_line_input("[bold green]you[/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit", "/q"):
                console.print("[dim]Goodbye.[/dim]")
                break
            elif cmd == "/help":
                console.print(HELP_TEXT)
            elif cmd == "/tools":
                cmd_tools()
            elif cmd == "/sessions":
                cmd_sessions()
            elif cmd == "/config":
                cmd_config(cfg)
            elif cmd == "/approve":
                cmd_approve(cfg)
            elif cmd == "/status":
                cmd_status(client)
            elif cmd == "/new":
                conv = Conversation(cwd=cfg.working_dir)
                console.print(f"[green]New session[/green] [cyan]{conv.session_id}[/cyan]")
            elif cmd == "/clear":
                conv.messages.clear()
                console.print("[dim]Cleared.[/dim]")
            elif cmd == "/compact":
                cmd_compact(conv, client)
            elif cmd == "/shortcut":
                cmd_shortcut(_main_py)
            elif cmd == "/npm":
                cmd_npm_quick(rest or "dev", cfg)
            elif cmd == "/browse":
                if rest:
                    cmd_browse_quick(rest, cfg)
                else:
                    console.print("[yellow]Usage:[/yellow] /browse <url>")
            elif cmd == "/ask":
                # Quick interactive ask: /ask <question>? opt1, opt2, opt3
                if "?" in rest:
                    q_part, opts_part = rest.split("?", 1)
                    q_part = q_part.strip() + "?"
                    opts = [o.strip() for o in opts_part.split(",") if o.strip()]
                elif rest:
                    # Just show a freetext fallback
                    q_part, opts = rest, ["Yes", "No", "Not sure"]
                else:
                    console.print("[yellow]Usage:[/yellow] /ask Question? opt1, opt2, opt3")
                    continue  # skip the rest
                if opts:
                    console.file.flush()
                    result = selector_ask(q_part, opts)
                    sel = result.get("selection")
                    if not result.get("cancelled") and sel:
                        console.print(f"  [green]→[/green] [bold]{sel}[/bold]")
            elif cmd == "/load":
                if not rest:
                    console.print("[yellow]Usage:[/yellow] /load <session-id>")
                else:
                    try:
                        conv = Conversation.load(rest)
                        console.print(f"[green]Loaded[/green] [cyan]{conv.session_id}[/cyan] ({len(conv.messages)} msgs)")
                    except FileNotFoundError:
                        console.print(f"[red]Not found:[/red] {rest}")
            elif cmd == "/model":
                if rest:
                    cfg.model = rest
                    client.model = rest
                    console.print(f"[green]Model →[/green] [bold]{rest}[/bold]")
                else:
                    console.print(f"Model: [bold]{cfg.model}[/bold]")
            elif cmd == "/cwd":
                if rest:
                    new_cwd = str(Path(rest).resolve())
                    if os.path.isdir(new_cwd):
                        cfg.working_dir = new_cwd
                        conv.cwd = new_cwd
                        console.print(f"[green]CWD →[/green] {new_cwd}")
                    else:
                        console.print(f"[red]Not a directory:[/red] {rest}")
                else:
                    console.print(f"CWD: [bold]{cfg.working_dir}[/bold]")
            elif cmd == "/set":
                kv = rest.split(maxsplit=1)
                if len(kv) != 2:
                    console.print("[yellow]Usage:[/yellow] /set <key> <value>")
                else:
                    try:
                        cfg.set(kv[0], kv[1])
                        console.print(f"[green]Set[/green] {kv[0]} = {kv[1]}")
                    except Exception as e:
                        console.print(f"[red]Error:[/red] {e}")
            else:
                console.print(f"[red]Unknown command:[/red] {cmd} — try /help")
            continue

        conv.add_user(user_input)
        run_agent_turn(client, conv, cfg)
