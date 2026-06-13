from __future__ import annotations

import json
import subprocess
import sys

import pytest

from chatalyst.core.config import AppConfig


def test_account_config_uses_isolated_runtime_paths(tmp_path):
    config = AppConfig.from_workspace(tmp_path, account="work")

    account_root = tmp_path / "accounts" / "work"
    assert config.workspace == tmp_path
    assert config.account == "work"
    assert config.account_dir == account_root
    assert config.database_path == account_root / "storage" / "chat_cache.db"
    assert config.profile_dir == account_root / "profile" / "chromium"
    assert config.logs_dir == account_root / "logs"
    assert config.exports_dir == account_root / "exports"
    assert config.config_dir == account_root / "config"
    assert config.plugins_dir == account_root / "plugins"
    assert config.runtime_lock_path == account_root / "runtime" / "default.lock"


@pytest.mark.parametrize("account", ["", "   ", "../work", "work/team", ".hidden", "bad account"])
def test_account_config_rejects_unsafe_account_names(tmp_path, account):
    with pytest.raises(ValueError, match="account"):
        AppConfig.from_workspace(tmp_path, account=account)


def test_create_and_list_accounts_cli(tmp_path):
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--create-account",
            "work",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    created = json.loads(create.stdout)
    assert created["ok"] is True
    assert created["account"] == "work"
    assert (tmp_path / "accounts" / "work" / "profile" / "chromium").is_dir()

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--list-accounts",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(listed.stdout)
    assert payload["workspace"] == str(tmp_path)
    assert payload["accounts"] == [{"name": "work", "path": str(tmp_path / "accounts" / "work")}]


def test_smoke_uses_selected_account_scope(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--account",
            "work",
            "--smoke",
            "--mcp-read-only",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    health = json.loads(payload["responses"][2]["result"]["content"][0]["text"])
    assert health["account"] == "work"
    assert health["workspace"] == str(tmp_path)
    assert health["account_dir"] == str(tmp_path / "accounts" / "work")


def test_doctor_without_account_reports_legacy_workspace(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatalyst.app",
            "--workspace",
            str(tmp_path),
            "--doctor",
            "--mcp-read-only",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["account"] is None
    assert payload["account_dir"] is None
    assert "account_dir" not in payload["paths"]
    assert payload["paths"]["database"]["path"] == str(tmp_path / "storage" / "chat_cache.db")
