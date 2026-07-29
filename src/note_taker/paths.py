from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def meetings_dir() -> Path:
    path = project_root() / ".meetings"
    path.mkdir(parents=True, exist_ok=True)
    return path
