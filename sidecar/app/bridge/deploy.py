"""Atomic RSS-Bridge PHP file deployment."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_VALID_SLUG = re.compile(r"^[A-Z][A-Za-z0-9]*Bridge$")


@dataclass
class DeployResult:
    deployed: bool = False
    path: str = ""
    errors: list[str] = field(default_factory=list)


def deploy_bridge(
    name: str,
    code: str,
    bridges_dir: str = "/app/bridges",
) -> DeployResult:
    """Write a bridge PHP file atomically. Returns DeployResult."""
    if not _VALID_SLUG.match(name):
        return DeployResult(
            errors=[
                f"Invalid bridge name {name!r}. "
                "Must match ^[A-Z][A-Za-z0-9]*Bridge$"
            ]
        )

    bridges_path = Path(bridges_dir).resolve()
    target = bridges_path / f"{name}.php"

    # Reject anything that escapes bridges_dir after resolution
    try:
        target.relative_to(bridges_path)
    except ValueError:
        return DeployResult(errors=[f"Path traversal rejected for: {name}"])

    try:
        bridges_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return DeployResult(errors=[f"Cannot create bridges directory: {exc}"])

    # Atomic write: tempfile in same dir + os.replace
    try:
        fd, tmp = tempfile.mkstemp(dir=str(bridges_path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(code)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        return DeployResult(errors=[f"Failed to write bridge file: {exc}"])

    return DeployResult(deployed=True, path=str(target))
