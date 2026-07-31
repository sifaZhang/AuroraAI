"""Project-wide environment configuration.

Configuration in the process environment takes precedence over the local
``.env`` file so deployment and test overrides remain reliable.
"""
from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: str | Path | None = None) -> bool:
    """Load simple ``KEY=VALUE`` settings from the project ``.env`` file."""
    env_path = Path(path) if path is not None else PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return False
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True
