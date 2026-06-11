from __future__ import annotations

import subprocess
import sys


def test_chatalyst_version_flag_reports_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "chatalyst.app", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "chatalyst 0.1.0"
    assert result.stderr == ""


def test_chatalyst_mcp_version_flag_reports_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "chatalyst.core.mcp_server", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "chatalyst-mcp 0.1.0"
    assert result.stderr == ""
