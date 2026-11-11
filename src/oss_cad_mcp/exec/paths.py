"""Workspace path validation and normalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oss_cad_mcp.errors import PathNotAllowed


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class PathPolicy:
    """Confines tool input and output paths to configured roots."""

    workspace: Path
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]

    @classmethod
    def create(
        cls,
        workspace: Path,
        *,
        read_roots: tuple[Path, ...] = (),
        write_roots: tuple[Path, ...] = (),
    ) -> PathPolicy:
        ws = workspace.expanduser().resolve()
        reads = tuple(dict.fromkeys([ws, *(p.expanduser().resolve() for p in read_roots)]))
        writes = tuple(dict.fromkeys([*(p.expanduser().resolve() for p in write_roots), ws]))
        return cls(workspace=ws, read_roots=reads, write_roots=writes)

    def _candidate(self, path: str | Path, base: Path | None) -> Path:
        raw = Path(path).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        return ((base or self.workspace) / raw).resolve()

    def resolve_read(self, path: str | Path, *, base: Path | None = None, must_exist: bool = True) -> Path:
        resolved = self._candidate(path, base)
        if not any(_is_relative_to(resolved, root) for root in self.read_roots):
            raise PathNotAllowed(
                f"Path is outside the allowed workspace: {path}",
                hint=f"Allowed read roots: {', '.join(str(root) for root in self.read_roots)}",
            )
        if must_exist and not resolved.exists():
            raise PathNotAllowed(f"Path does not exist: {path}")
        return resolved

    def resolve_write(self, path: str | Path, *, base: Path | None = None) -> Path:
        resolved = self._candidate(path, base)
        if not any(_is_relative_to(resolved, root) for root in self.write_roots):
            raise PathNotAllowed(
                f"Path is outside the writable workspace: {path}",
                hint=f"Allowed write roots: {', '.join(str(root) for root in self.write_roots)}",
            )
        return resolved

    def display(self, path: str | Path) -> str:
        resolved = Path(path).expanduser()
        resolved = (self.workspace / resolved).resolve() if not resolved.is_absolute() else resolved.resolve()
        if _is_relative_to(resolved, self.workspace):
            return resolved.relative_to(self.workspace).as_posix()
        return resolved.as_posix()

    def display_or_absolute(self, path: str | Path) -> str:
        return self.display(path)

    def glob(self, pattern: str, *, base: Path | None = None, must_exist: bool = False) -> list[Path]:
        base_dir = (base or self.workspace).expanduser().resolve()
        pattern_path = Path(pattern)
        if pattern_path.is_absolute():
            anchor = Path(pattern_path.anchor)
            relative = pattern_path.relative_to(anchor)
            matches = sorted(anchor.glob(str(relative)))
        else:
            matches = sorted(base_dir.glob(pattern))
        result: list[Path] = []
        for match in matches:
            if not match.is_file():
                continue
            resolved = match.resolve()
            if any(_is_relative_to(resolved, root) for root in self.read_roots):
                result.append(resolved)
        if must_exist and not result:
            raise PathNotAllowed(f"No files matched pattern: {pattern}")
        return result
