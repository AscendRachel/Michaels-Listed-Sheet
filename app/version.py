"""The release number, read from the VERSION file at the top of the project (it is copied into the Docker image).

Showing it on the sign-in page lets whoever updates the server see that the new image is really the one running."""

from __future__ import annotations

from pathlib import Path


def current() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"                      # running from a copy that has no VERSION file


VERSION = current()
