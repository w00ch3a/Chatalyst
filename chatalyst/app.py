from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import termios
import tty
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from loguru import logger

from chatalyst.core.version import package_version

if TYPE_CHECKING:
    from chatalyst.core.config import AppConfig

DEFAULT_MCP_LIVE_RESULT_MESSAGE_LIMIT = 20
FRUGAL_MCP_LIVE_RESULT_MESSAGE_LIMIT = 6


async def run_interactive_login(config: AppConfig) -> int:
    """Open the persistent browser profile and wait for manual ChatGPT login."""

    from chatalyst.core.browser import BrowserController
    from chatalyst.core.cache import ChatCache
    from chatalyst.core.chatgpt import ChatGPTService
    from chatalyst.core.models import LoginState
    from chatalyst.core.runtime import RuntimeLock

    config.ensure_runtime_dirs()
    runtime_lock = RuntimeLock(config.runtime_lock_path)
    runtime_lock.acquire()
    cache = ChatCache(config.database_path)
    browser: BrowserController | None = None
    try:
        logger.add(config.logs_dir / "chatgpt-tui.log", rotation="2 MB", retention=5)
        cache.initialize()
        browser = BrowserController(config)
        chatgpt = ChatGPTService(config, browser, cache)

        print("Opening ChatGPT in a persistent Chromium profile...")
        await browser.start(visible=True)
        await browser.open_chatgpt()
        print()
        print("Log in through the Chromium window.")
        print("When ChatGPT is fully open, return here and press Enter.")
        print("Leave this terminal waiting while you complete browser login, 2FA, passkeys, etc.")
        try:
            confirmed = await asyncio.to_thread(wait_for_terminal_return)
        except KeyboardInterrupt:
            print()
            print("Login wait cancelled; leaving the browser profile unchanged.")
            return 130
        if not confirmed:
            print("No terminal input was available; leaving the browser profile unchanged.")
            return 2

        status = await chatgpt.status()
        if status.login_state is LoginState.LOGGED_IN:
            storage_state = await browser.storage_state_path()
            print(f"Login detected and saved in {config.profile_dir}")
            print(f"Storage state snapshot written to {storage_state}")
            return 0

        print(f"Login was not detected yet: {status.login_state.value}")
        print("Run `chatalyst --login` again, or run `chatalyst --browser-mode visible`.")
        return 1
    finally:
        if browser is not None:
            await browser.stop()
        cache.close()
        runtime_lock.release()


def wait_for_terminal_return(stdin: TextIO | None = None) -> bool:
    """Wait for Return in terminals that send either LF or CR."""

    stream = stdin or sys.stdin
    if not stream.isatty():
        while True:
            char = stream.read(1)
            if char == "":
                return False
            if char in {"\n", "\r"}:
                return True

    fd = stream.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = termios.tcgetattr(fd)
    new_attrs[3] &= ~(termios.ICANON | termios.ECHO)
    new_attrs[6][termios.VMIN] = 1
    new_attrs[6][termios.VTIME] = 0
    try:
        tty.setcbreak(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, new_attrs)
        while True:
            char = stream.read(1)
            if char in {"\n", "\r"}:
                print()
                return True
            if char == "\x03":
                raise KeyboardInterrupt
            if char == "\x04":
                print()
                return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def run_doctor(config: AppConfig, *, include_mcp: bool, max_text_chars: int) -> int:
    """Run local configuration checks without opening ChatGPT."""

    from chatalyst.core.mcp_server import ChatalystMCPServer

    config.ensure_runtime_dirs()
    server = ChatalystMCPServer(config, read_only=config.offline, max_text_chars=max_text_chars)
    try:
        counts = server._cache_counts()
        scope = server._tool_get_scope({})
        tools = server._tools() if include_mcp else []
        plugin_names = list(server.plugins.names)
        plugin_count = len(server.plugins.plugins)
    finally:
        server.cache.close()

    paths = {
        "workspace": config.workspace,
        "database": config.database_path,
        "profile": config.profile_dir,
        "logs": config.logs_dir,
        "exports": config.exports_dir,
        "snippets": config.snippets_dir,
    }
    if config.account_dir is not None:
        paths["account_dir"] = config.account_dir
    path_status = {
        name: {
            "path": str(path),
            "exists": path.exists(),
            "private_mode": _private_mode(path),
        }
        for name, path in paths.items()
    }
    payload = {
        "ok": True,
        "workspace": str(config.workspace),
        "account": config.account,
        "account_dir": str(config.account_dir) if config.account_dir else None,
        "offline": config.offline,
        "browser_mode": config.browser_mode.value,
        "browser_profile": config.browser_profile.value,
        "commands": {
            "chatalyst": shutil.which("chatalyst"),
            "chatalyst-mcp": shutil.which("chatalyst-mcp"),
            "chatgpt-tui": shutil.which("chatgpt-tui"),
        },
        "paths": path_status,
        "cache_counts": counts,
        "runtime_lock": server._runtime_lock_status(),
        "processes": server._process_status(),
        "plugins": {"count": plugin_count, "names": plugin_names},
        "scope": scope,
        "mcp": {
            "checked": include_mcp,
            "tool_count": len(tools),
            "tools": [tool["name"] for tool in tools],
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def run_create_account(workspace: Path, account: str) -> int:
    from chatalyst.core.config import AppConfig

    config = AppConfig.from_workspace(workspace, account=account)
    config.ensure_runtime_dirs()
    payload = {
        "ok": True,
        "account": config.account,
        "account_dir": str(config.account_dir),
        "profile_dir": str(config.profile_dir),
        "database_path": str(config.database_path),
        "plugins_dir": str(config.plugins_dir),
    }
    print(json.dumps(payload, indent=2))
    return 0


def run_list_accounts(workspace: Path) -> int:
    from chatalyst.core.config import validate_account_name

    root = workspace.expanduser().resolve()
    accounts_dir = root / "accounts"
    accounts: list[dict[str, str]] = []
    if accounts_dir.exists():
        for path in sorted(accounts_dir.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_dir():
                continue
            try:
                account = validate_account_name(path.name)
            except ValueError:
                continue
            if account is not None:
                accounts.append({"name": account, "path": str(path.resolve())})
    print(json.dumps({"workspace": str(root), "accounts": accounts}, indent=2))
    return 0


def run_mcp_smoke(config: AppConfig, *, read_only: bool, max_text_chars: int) -> int:
    """Run a local MCP JSON-RPC smoke test without opening ChatGPT."""

    from chatalyst.core.mcp_server import ChatalystMCPServer

    config.ensure_runtime_dirs()
    server = ChatalystMCPServer(config, read_only=read_only, max_text_chars=max_text_chars)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "chatalyst_health", "arguments": {}},
        },
    ]

    async def _run() -> list[dict[str, object] | None]:
        try:
            return [await server.handle(request) for request in requests]
        finally:
            await server.close()

    responses = asyncio.run(_run())
    failures = [response for response in responses if response and response.get("error")]
    payload = {
        "ok": not failures,
        "responses": responses,
    }
    print(json.dumps(payload, indent=2))
    return 1 if failures else 0


def run_set_project_alias(config: AppConfig, *, alias: str, target: str) -> int:
    config.ensure_runtime_dirs()
    path = config.project_aliases_path
    aliases: dict[str, str] = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            aliases = {str(key): str(value) for key, value in raw.items()}
    aliases[alias.strip()] = target.strip()
    path.write_text(json.dumps(aliases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(path),
                "alias": alias.strip(),
                "target": "[redacted-project-reference]",
            },
            indent=2,
        )
    )
    return 0


async def run_project_doctor(config: AppConfig, *, project_reference: str | None) -> int:
    """Inspect visible ChatGPT projects and optional project scope opening."""

    from chatalyst.core.browser import BrowserController
    from chatalyst.core.cache import ChatCache
    from chatalyst.core.chatgpt import ChatGPTService
    from chatalyst.core.privacy import redact_project_refs
    from chatalyst.core.project_aliases import ProjectAliasResolver

    config.ensure_runtime_dirs()
    cache = ChatCache(config.database_path)
    cache.initialize()
    browser = BrowserController(config)
    chatgpt = ChatGPTService(config, browser, cache)
    project_aliases = ProjectAliasResolver(config)
    resolved_project = project_aliases.resolve(project_reference)
    payload: dict[str, object] = {
        "ok": False,
        "requested_project": resolved_project.display if resolved_project else None,
        "project_alias_used": resolved_project.alias_used if resolved_project else None,
        "browser": None,
        "url": None,
        "projects": [],
        "project_diagnostics": None,
        "open_project": None,
    }
    try:
        status = await chatgpt.status()
        payload["browser"] = status.model_dump(mode="json")
        page = await browser.start()
        payload["url"] = page.url
        projects = await chatgpt.extract_projects(page)
        for project in projects:
            cache.upsert_project(project)
        payload["projects"] = redact_project_refs(
            [project.model_dump(mode="json") for project in projects]
        )
        if not projects:
            payload["project_diagnostics"] = await chatgpt.project_diagnostics(page)
        if resolved_project:
            try:
                await chatgpt._open_project(page, resolved_project.resolved)  # noqa: SLF001
                scope = await chatgpt.verify_project_scope(resolved_project.resolved)
                payload["open_project"] = redact_project_refs(
                    {
                        "requested_project": resolved_project.display,
                        "alias_used": resolved_project.alias_used,
                        "verified": scope.verified,
                        "reason": scope.reason,
                        "url": scope.url,
                    }
                )
                payload["ok"] = True
            except Exception as exc:
                payload["open_project"] = {"ok": False, "error": str(exc)}
        else:
            payload["ok"] = True
    finally:
        await browser.park_after_work()
        await browser.stop()
        cache.close()
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


def _private_mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(path.stat().st_mode & 0o777)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatalyst")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
        help="Show the installed Chatalyst version and exit.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Chatalyst workspace containing profile/, storage/, exports/, and work/.",
    )
    parser.add_argument(
        "--account",
        help=(
            "Use an isolated account under workspace/accounts/ACCOUNT. Each account "
            "has its own Chromium profile, SQLite vault, plugins, logs, and exports."
        ),
    )
    parser.add_argument(
        "--create-account",
        metavar="ACCOUNT",
        help="Create an isolated account workspace and exit.",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="List isolated account workspaces and exit.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Browse cached chats without browser access.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open the persistent Chromium profile for manual ChatGPT login, then wait.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run/check the local-vault MCP server instead of the TUI.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check local Chatalyst workspace, install, vault, and optional MCP schema.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a local MCP smoke test without opening ChatGPT.",
    )
    parser.add_argument(
        "--project-doctor",
        action="store_true",
        help="Open ChatGPT and report visible projects plus optional project scope.",
    )
    parser.add_argument(
        "--set-project-alias",
        nargs=2,
        metavar=("ALIAS", "PROJECT_REF"),
        help="Store a private local project alias in config/project_aliases.json.",
    )
    parser.add_argument(
        "--repair-stale-lock",
        action="store_true",
        help="Remove stale unlocked runtime lock metadata before doctor/smoke/project-doctor.",
    )
    parser.add_argument(
        "--kill-extra-processes",
        action="store_true",
        help="Terminate duplicate Chatalyst MCP processes for this workspace and exit.",
    )
    parser.add_argument(
        "--kill-workspace-mcp-processes",
        action="store_true",
        help="Terminate all Chatalyst MCP processes for this workspace and exit.",
    )
    parser.add_argument(
        "--mcp-read-only",
        action="store_true",
        help="When used with --mcp, expose only read-only vault tools.",
    )
    parser.add_argument(
        "--mcp-max-request-bytes",
        type=int,
        default=1_000_000,
        help="When used with --mcp, cap each JSON-RPC request read from stdin.",
    )
    parser.add_argument(
        "--mcp-max-text-chars",
        type=int,
        default=100_000,
        help="When used with --mcp, cap text bodies accepted by write-capable tools.",
    )
    parser.add_argument(
        "--assistant-response-timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum wait for ChatGPT to start/finish a live assistant response.",
    )
    parser.add_argument(
        "--mcp-live-response-timeout-seconds",
        type=float,
        default=180.0,
        help="Default MCP live send/reply wait before returning submitted_no_response.",
    )
    parser.add_argument(
        "--mcp-live-result-message-limit",
        type=int,
        default=None,
        help="Recent messages returned by live MCP send/reply tools; full history remains cached.",
    )
    parser.add_argument(
        "--mcp-token-frugal",
        action="store_true",
        help=(
            "Reduce default live MCP result payloads and report prompt-size budgeting "
            "metadata for agents."
        ),
    )
    parser.add_argument(
        "--mcp-prompt-warning-tokens",
        type=int,
        default=4_000,
        help="Approximate prompt-token threshold reported by MCP prompt budgeting.",
    )
    parser.add_argument(
        "--mcp-default-conversation",
        help=(
            "Optional MCP default conversation id, URL, or title used when reply tools "
            "omit conversation_id."
        ),
    )
    parser.add_argument(
        "--mcp-default-project",
        help=(
            "Optional MCP default project name; reply tools use the most recent cached "
            "conversation in that project when conversation_id is omitted."
        ),
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose diagnostics.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Shortcut for --browser-mode headless. Best for SSH after login is saved.",
    )
    parser.add_argument(
        "--browser-mode",
        choices=("auto", "provider", "background", "visible", "headless", "sleep"),
        default="auto",
        help=(
            "Browser lifecycle: auto shows login then goes headless; provider uses a "
            "disposable hidden headed Chromium provider for live work; visible always "
            "shows; headless always hides; sleep closes Chromium between live operations."
        ),
    )
    parser.add_argument(
        "--browser-profile",
        choices=("standard", "lite", "ultralight"),
        default="standard",
        help="Browser resource policy. Ultralight blocks more assets and keeps less DOM visible.",
    )
    parser.add_argument(
        "--host-mode",
        choices=("single",),
        default="single",
        help="Network host posture. Currently single-user SSH hosting with a runtime lock.",
    )
    return parser


def _mcp_live_result_message_limit(args: argparse.Namespace) -> int:
    if args.mcp_live_result_message_limit is not None:
        return int(args.mcp_live_result_message_limit)
    if args.mcp_token_frugal:
        return FRUGAL_MCP_LIVE_RESULT_MESSAGE_LIMIT
    return DEFAULT_MCP_LIVE_RESULT_MESSAGE_LIMIT


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    from chatalyst.core.config import AppConfig, validate_account_name

    if args.login and args.mcp:
        parser.error("--login cannot be combined with --mcp")
    if args.login and args.doctor:
        parser.error("--login cannot be combined with --doctor")
    if args.login and args.smoke:
        parser.error("--login cannot be combined with --smoke")
    if args.login and args.project_doctor:
        parser.error("--login cannot be combined with --project-doctor")
    if args.login and args.set_project_alias:
        parser.error("--login cannot be combined with --set-project-alias")
    browser_mode = "headless" if args.headless else args.browser_mode
    workspace = args.workspace.expanduser().resolve()
    try:
        account = validate_account_name(args.account)
        create_account = validate_account_name(args.create_account)
    except ValueError as exc:
        parser.error(str(exc))
    if args.list_accounts:
        raise SystemExit(run_list_accounts(workspace))
    if create_account:
        raise SystemExit(run_create_account(workspace, create_account))
    if args.repair_stale_lock:
        from chatalyst.core.runtime import RuntimeLock

        probe_config = AppConfig.from_workspace(workspace, account=account)
        RuntimeLock.clean_stale(probe_config.runtime_lock_path)
    if args.kill_extra_processes:
        from chatalyst.core.processes import kill_extra_chatalyst_processes

        probe_config = AppConfig.from_workspace(workspace, account=account)
        print({"killed_pids": kill_extra_chatalyst_processes(probe_config.workspace)})
        raise SystemExit(0)
    if args.kill_workspace_mcp_processes:
        from chatalyst.core.processes import kill_workspace_mcp_processes

        probe_config = AppConfig.from_workspace(workspace, account=account)
        print({"killed_pids": kill_workspace_mcp_processes(probe_config.workspace)})
        raise SystemExit(0)
    if args.set_project_alias:
        config = AppConfig.from_workspace(workspace, account=account)
        raise SystemExit(
            run_set_project_alias(
                config,
                alias=args.set_project_alias[0],
                target=args.set_project_alias[1],
            )
        )
    if args.doctor:
        from chatalyst.core.config import live_mcp_browser_profile

        mcp_browser_mode = browser_mode
        if (
            args.mcp
            and not args.mcp_read_only
            and not args.headless
            and args.browser_mode == "auto"
        ):
            mcp_browser_mode = "provider"
        mcp_browser_profile = live_mcp_browser_profile(
            mcp_browser_mode, args.browser_profile
        )
        config = AppConfig.from_workspace(
            workspace,
            offline=args.offline or args.mcp_read_only,
            debug=args.debug,
            headless=args.headless,
            browser_mode=mcp_browser_mode,
            browser_profile=mcp_browser_profile,
            account=account,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": _mcp_live_result_message_limit(args),
                "mcp_token_frugal": args.mcp_token_frugal,
                "mcp_prompt_warning_tokens": args.mcp_prompt_warning_tokens,
                "mcp_default_conversation": args.mcp_default_conversation,
                "mcp_default_project": args.mcp_default_project,
            }
        )
        raise SystemExit(
            run_doctor(config, include_mcp=args.mcp, max_text_chars=args.mcp_max_text_chars)
        )
    if args.smoke:
        from chatalyst.core.config import live_mcp_browser_profile

        mcp_browser_mode = browser_mode
        if not args.mcp_read_only and not args.headless and args.browser_mode == "auto":
            mcp_browser_mode = "provider"
        mcp_browser_profile = live_mcp_browser_profile(
            mcp_browser_mode, args.browser_profile
        )
        config = AppConfig.from_workspace(
            workspace,
            offline=args.offline or args.mcp_read_only,
            debug=args.debug,
            headless=args.headless,
            browser_mode=mcp_browser_mode,
            browser_profile=mcp_browser_profile,
            account=account,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": _mcp_live_result_message_limit(args),
                "mcp_token_frugal": args.mcp_token_frugal,
                "mcp_prompt_warning_tokens": args.mcp_prompt_warning_tokens,
                "mcp_default_conversation": args.mcp_default_conversation,
                "mcp_default_project": args.mcp_default_project,
            }
        )
        raise SystemExit(
            run_mcp_smoke(
                config,
                read_only=args.offline or args.mcp_read_only,
                max_text_chars=args.mcp_max_text_chars,
            )
        )
    if args.project_doctor:
        from chatalyst.core.config import live_mcp_browser_profile
        from chatalyst.core.runtime import RuntimeLockError

        mcp_browser_profile = live_mcp_browser_profile(browser_mode, args.browser_profile)

        config = AppConfig.from_workspace(
            workspace,
            offline=False,
            debug=args.debug,
            headless=args.headless,
            browser_mode=browser_mode,
            browser_profile=mcp_browser_profile,
            account=account,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": _mcp_live_result_message_limit(args),
                "mcp_token_frugal": args.mcp_token_frugal,
                "mcp_prompt_warning_tokens": args.mcp_prompt_warning_tokens,
                "mcp_default_project": args.mcp_default_project,
            }
        )
        try:
            raise SystemExit(
                asyncio.run(run_project_doctor(config, project_reference=args.mcp_default_project))
            )
        except RuntimeLockError as exc:
            print(exc)
            raise SystemExit(2) from exc
    if args.login:
        from chatalyst.core.runtime import RuntimeLockError

        config = AppConfig.from_workspace(
            workspace,
            offline=False,
            debug=args.debug,
            headless=False,
            browser_mode="visible",
            browser_profile=args.browser_profile,
            account=account,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": _mcp_live_result_message_limit(args),
                "mcp_token_frugal": args.mcp_token_frugal,
                "mcp_prompt_warning_tokens": args.mcp_prompt_warning_tokens,
            }
        )
        try:
            raise SystemExit(asyncio.run(run_interactive_login(config)))
        except RuntimeLockError as exc:
            print(exc)
            raise SystemExit(2) from exc
    if args.mcp:
        from chatalyst.core.config import live_mcp_browser_profile
        from chatalyst.core.mcp_server import ChatalystMCPServer, run_stdio

        mcp_browser_mode = browser_mode
        if not args.mcp_read_only and not args.headless and args.browser_mode == "auto":
            mcp_browser_mode = "provider"
        mcp_browser_profile = live_mcp_browser_profile(
            mcp_browser_mode, args.browser_profile
        )
        config = AppConfig.from_workspace(
            workspace,
            offline=args.offline or args.mcp_read_only,
            debug=args.debug,
            headless=args.headless,
            browser_mode=mcp_browser_mode,
            browser_profile=mcp_browser_profile,
            account=account,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": _mcp_live_result_message_limit(args),
                "mcp_token_frugal": args.mcp_token_frugal,
                "mcp_prompt_warning_tokens": args.mcp_prompt_warning_tokens,
                "mcp_default_conversation": args.mcp_default_conversation,
                "mcp_default_project": args.mcp_default_project,
            }
        )
        server = ChatalystMCPServer(
            config,
            read_only=args.offline or args.mcp_read_only,
            max_text_chars=args.mcp_max_text_chars,
        )
        raise SystemExit(run_stdio(server, max_request_bytes=args.mcp_max_request_bytes))
    config = AppConfig.from_workspace(
        workspace,
        offline=args.offline,
        debug=args.debug,
        headless=args.headless,
        browser_mode=browser_mode,
        browser_profile=args.browser_profile,
        account=account,
    ).model_copy(
        update={
            "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
            "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
            "mcp_live_result_message_limit": _mcp_live_result_message_limit(args),
            "mcp_token_frugal": args.mcp_token_frugal,
            "mcp_prompt_warning_tokens": args.mcp_prompt_warning_tokens,
        }
    )
    try:
        from chatalyst.core.runtime import RuntimeLockError
        from chatalyst.tui_app import ChatGPTTUI

        ChatGPTTUI(config).run()
    except RuntimeLockError as exc:
        print(exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
