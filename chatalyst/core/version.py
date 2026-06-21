from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    try:
        return version("chatalyst")
    except PackageNotFoundError:
        return "0.3.0"
