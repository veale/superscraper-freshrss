"""Atomic RSS-Bridge PHP file deployment."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.services.config import ServiceConfig

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


def _local_bridges_writable(bridges_dir: str) -> bool:
    p = Path(bridges_dir)
    if not p.exists():
        return False
    return os.access(str(p), os.W_OK)


async def deploy_bridge_remote(
    name: str,
    code: str,
    *,
    services: ServiceConfig,
    bridges_dir: str = "/app/bridges",
) -> DeployResult:
    """Deploy a bridge either locally (shared volume) or via a remote POST.

    Precedence:
      1. No remote URL, or local bridges dir exists and is writable → local write.
      2. Otherwise POST to `{services.rss_bridge_url}/deploy-bridge`.

    The official RSS-Bridge image does NOT ship `/deploy-bridge`; the remote
    path requires a custom image or reverse-proxy sidecar. Local volume remains
    the default/only option when neither is configured.
    """
    if not _VALID_SLUG.match(name):
        return DeployResult(
            errors=[
                f"Invalid bridge name {name!r}. "
                "Must match ^[A-Z][A-Za-z0-9]*Bridge$"
            ]
        )

    services = services.normalised()

    if not services.rss_bridge_url or _local_bridges_writable(bridges_dir):
        return deploy_bridge(name, code, bridges_dir=bridges_dir)

    headers = {"Accept": "application/json"}
    if services.auth_token:
        headers["Authorization"] = f"Bearer {services.auth_token}"

    endpoint = f"{services.rss_bridge_url}/deploy-bridge"
    feed_url = (
        f"{services.rss_bridge_url}/?action=display&bridge={name}&format=Atom"
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.post(
                endpoint,
                json={"name": name, "php_code": code},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        return DeployResult(errors=[f"Remote bridge deploy failed: {exc}"])

    if 200 <= resp.status_code < 300:
        return DeployResult(deployed=True, path=feed_url)

    return DeployResult(
        errors=[f"Remote bridge deploy returned HTTP {resp.status_code}"]
    )
