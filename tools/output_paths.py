"""Read-only validation of the exact game root selected for an output copy."""
from __future__ import annotations

from itertools import islice
from pathlib import Path
import stat
from typing import Iterable


class OutputPathError(ValueError):
    def __init__(self, code: str, path: Path, *, entries: tuple[str, ...] = (),
                 more_entries: bool = False, source: Path | None = None,
                 detail: str = "") -> None:
        self.code = code
        self.path = path
        self.entries = entries
        self.more_entries = more_entries
        self.source = source
        self.detail = detail
        super().__init__(f"Output directory {code}: {path}" + (f" ({detail})" if detail else ""))


def _resolve(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise OutputPathError("unreadable", candidate, detail=str(exc)) from exc


def normalize_output_root(path: str | Path) -> Path:
    """Match the worker's appdata-to-game-root rule without changing other names."""
    resolved = _resolve(path)
    return resolved.parent if resolved.name.casefold() == "appdata" else resolved


def _check_directory(path: Path) -> bool:
    """Return False for a missing path; files and inaccessible paths are errors."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    except NotADirectoryError as exc:
        raise OutputPathError("not_directory", path, detail=str(exc)) from exc
    except OSError as exc:
        raise OutputPathError("unreadable", path, detail=str(exc)) from exc
    if not stat.S_ISDIR(mode):
        raise OutputPathError("not_directory", path)
    return True


def validate_output_directory(path: str | Path, source_roots: Iterable[str | Path] = (),
                              tool_root: str | Path | None = None) -> Path:
    """Accept an empty/new exact destination; never create, rename or remove files.

    Source appdata inputs protect their outer client root. Resolve aliases before
    checking containment, and inspect the game root the worker will actually copy
    into rather than accidentally accepting an empty inner appdata directory.
    """
    selected = _resolve(path)
    _check_directory(selected)
    output = normalize_output_root(selected)
    for source in source_roots:
        if not str(source).strip():
            continue
        root = normalize_output_root(source)
        if output == root or root in output.parents:
            raise OutputPathError("source_overlap", output, source=root)
    if tool_root is not None:
        root = _resolve(tool_root)
        if output == root or root in output.parents:
            raise OutputPathError("tool_overlap", output, source=root)
    if not _check_directory(output):
        # Check the nearest existing parent, including files in the path. The
        # worker may create absent parents later; validation itself is read-only.
        for parent in output.parents:
            if _check_directory(parent):
                break
        return output
    try:
        entries = tuple(child.name for child in islice(output.iterdir(), 6))
    except OSError as exc:
        raise OutputPathError("unreadable", output, detail=str(exc)) from exc
    if entries:
        raise OutputPathError("nonempty", output, entries=entries[:5], more_entries=len(entries) > 5)
    return output
