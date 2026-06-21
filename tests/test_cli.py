from __future__ import annotations

import json
import subprocess
import sys


def test_chatalyst_version_flag_reports_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "chatalyst.app", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "chatalyst 0.3.1"
    assert result.stderr == ""


def test_chatalyst_mcp_version_flag_reports_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "chatalyst.core.mcp_server", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "chatalyst-mcp 0.3.1"
    assert result.stderr == ""


def test_mcp_version_does_not_import_playwright():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import chatalyst.core.mcp_server; "
                "print(any(name.startswith('playwright') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_mcp_version_does_not_import_search_or_export_engines():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import chatalyst.core.mcp_server; "
                "print(any(name.startswith(('rapidfuzz', 'markdown_it')) for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_mcp_module_import_does_not_import_pydantic_config():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import chatalyst.core.mcp_server; "
                "print(any(name.startswith('pydantic') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_console_version_does_not_import_textual():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from chatalyst.core.cli import main; "
                "print(any(name.startswith('textual') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_console_module_import_does_not_import_pydantic_config():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import chatalyst.app; "
                "print(any(name.startswith('pydantic') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_chatalyst_smoke_reports_success(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--smoke",
            "--mcp-read-only",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert [response["id"] for response in payload["responses"]] == [1, 2, 3]


def test_chatalyst_smoke_reports_token_frugal_defaults(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--smoke",
            "--mcp-read-only",
            "--mcp-token-frugal",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    health = json.loads(payload["responses"][2]["result"]["content"][0]["text"])
    assert health["token_frugal"] is True
    assert health["live_result_message_limit"] == 6


def test_chatalyst_set_project_alias_writes_private_alias_file(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--set-project-alias",
            "work",
            "https://chatgpt.com/g/private-project",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    alias_path = tmp_path / "config" / "project_aliases.json"
    aliases = json.loads(alias_path.read_text(encoding="utf-8"))
    assert payload["target"] == "[redacted-project-reference]"
    assert aliases == {"work": "https://chatgpt.com/g/private-project"}
    assert oct(alias_path.stat().st_mode & 0o777) == "0o600"
