"""File storage behind two functions (sprint S0.6).

Local disk today. Moving to S3-compatible object storage later should touch
this module and nothing else.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class UnsupportedFileType(ValueError):
    pass


def _root() -> Path:
    root = Path(current_app.config["UPLOAD_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_image(file: FileStorage, subdir: str = "covers") -> str:
    """Store an uploaded image, returning the DB-persisted relative path."""
    name = secure_filename(file.filename or "")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise UnsupportedFileType(ext or "unknown")

    target_dir = _root() / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{secrets.token_hex(16)}{ext}"
    file.save(target_dir / stored)
    return f"{subdir}/{stored}"


def resolve(relative_path: str) -> Path:
    """Absolute path for a stored file, guarded against traversal."""
    root = _root().resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Path escapes the upload root")
    return candidate


def delete(relative_path: str) -> None:
    try:
        resolve(relative_path).unlink(missing_ok=True)
    except ValueError:
        pass
