"""SFTP-based RSS-Bridge PHP file deployment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import asyncssh

from app.services.config import ServiceConfig

_VALID_SLUG = re.compile(r"^[A-Z][A-Za-z0-9]*Bridge$")


@dataclass
class SftpDeployResult:
    """Result of an SFTP deployment operation."""
    deployed: bool = False
    path: str = ""
    errors: list[str] = field(default_factory=list)


async def test_sftp_connection(
    host: str,
    port: int,
    username: str,
    key_path: Optional[str],
    target_dir: str,
) -> SftpDeployResult:
    """Test SFTP connection to a remote host."""
    errors = []

    # Validate inputs
    if not host:
        errors.append("Host is required")
    if not username:
        errors.append("Username is required")
    if not target_dir:
        errors.append("Target directory is required")

    if errors:
        return SftpDeployResult(errors=errors)

    # Resolve key path
    if key_path:
        key_path = os.path.expanduser(key_path)
        if not os.path.exists(key_path):
            return SftpDeployResult(errors=[f"SSH key not found: {key_path}"])

    try:
        async with asyncssh.connect(
            host=host,
            port=port,
            username=username,
            client_keys=[key_path] if key_path else None,
            known_hosts=None,  # Disable host key verification for simplicity
        ) as conn:
            # Try to list the target directory to verify access
            await conn.listdir(target_dir)

        return SftpDeployResult(deployed=True, path=target_dir)

    except asyncssh.SSHError as exc:
        return SftpDeployResult(errors=[f"SSH connection failed: {exc}"])
    except OSError as exc:
        return SftpDeployResult(errors=[f"File error: {exc}"])
    except Exception as exc:
        return SftpDeployResult(errors=[f"Unexpected error: {exc}"])


async def deploy_bridge_via_sftp(
    name: str,
    code: str,
    host: str,
    port: int,
    username: str,
    key_path: Optional[str],
    target_dir: str,
) -> SftpDeployResult:
    """Deploy a bridge PHP file via SFTP to a remote host."""
    # Validate bridge name
    if not _VALID_SLUG.match(name):
        return SftpDeployResult(
            errors=[
                f"Invalid bridge name {name!r}. "
                "Must match ^[A-Z][A-Za-z0-9]*Bridge$"
            ]
        )

    # Validate inputs
    errors = []
    if not host:
        errors.append("Host is required")
    if not username:
        errors.append("Username is required")
    if not target_dir:
        errors.append("Target directory is required")

    if errors:
        return SftpDeployResult(errors=errors)

    # Resolve key path
    if key_path:
        key_path = os.path.expanduser(key_path)
        if not os.path.exists(key_path):
            return SftpDeployResult(errors=[f"SSH key not found: {key_path}"])

    target_file = f"{name}.php"
    remote_path = f"{target_dir}/{target_file}"

    try:
        async with asyncssh.connect(
            host=host,
            port=port,
            username=username,
            client_keys=[key_path] if key_path else None,
            known_hosts=None,
        ) as conn:
            # Write the file via SFTP
            async with conn.open_sftp() as sftp:
                # Ensure target directory exists
                try:
                    await sftp.stat(target_dir)
                except FileNotFoundError:
                    # Try to create the directory
                    await sftp.mkdir(target_dir, mode=0o755)

                # Write the file
                with await sftp.open(remote_path, 'w') as f:
                    f.write(code)

        return SftpDeployResult(deployed=True, path=remote_path)

    except asyncssh.SSHError as exc:
        return SftpDeployResult(errors=[f"SSH connection failed: {exc}"])
    except OSError as exc:
        return SftpDeployResult(errors=[f"File error: {exc}"])
    except Exception as exc:
        return SftpDeployResult(errors=[f"Unexpected error: {exc}"])


def get_sftp_config(ext_config: dict) -> Optional[dict]:
    """Extract SFTP configuration from extension config."""
    sftp_host = ext_config.get('sftp_host', '').strip()
    if not sftp_host:
        return None

    return {
        'host': sftp_host,
        'port': int(ext_config.get('sftp_port') or 22),
        'username': ext_config.get('sftp_user', '').strip(),
        'key_path': ext_config.get('sftp_key_path', '').strip() or None,
        'target_dir': ext_config.get('sftp_target_dir', '').strip(),
    }