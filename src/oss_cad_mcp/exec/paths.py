"""Workspace path validation and normalization."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from oss_cad_mcp.errors import PathNotAllowed

_PATH_SUFFIXES = {
    ".v",
    ".vh",
    ".sv",
    ".svh",
    ".vhd",
    ".vhdl",
    ".f",
    ".pcf",
    ".lpf",
    ".cst",
    ".sdc",
    ".xdc",
    ".qsf",
    ".json",
    ".vcd",
    ".fst",
    ".ghw",
    ".asc",
    ".bin",
    ".bit",
    ".config",
    ".txt",
    ".log",
    ".csv",
    ".ys",
    ".sby",
    ".eqy",
    ".mcy",
    ".il",
    ".blif",
}
_PATH_OPTIONS = {
    "-o",
    "-f",
    "-s",
    "-c",
    "--output",
    "--input",
    "--json",
    "--out",
    "--file",
    "--script",
    "--write",
    "--read",
    "--gate",
    "--gold",
    "--top",
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _looks_like_path(token: str) -> bool:
    if not token or token == "-":
        return False
    path = Path(token)
    return (
        path.is_absolute()
        or token.startswith(("~", ".", ".."))
        or "/" in token
        or "\\" in token
        or path.suffix.lower() in _PATH_SUFFIXES
    )


def _argv_path_candidates(args: list[str]) -> list[str]:
    candidates: list[str] = []
    expect_path = False
    for raw in args:
        if expect_path:
            expect_path = False
            candidates.append(raw)
            continue
        if raw in _PATH_OPTIONS:
            expect_path = True
            continue
        token = raw
        for prefix in ("+incdir+", "+define+", "-I", "-D"):
            if token.startswith(prefix) and len(token) > len(prefix):
                token = token[len(prefix) :]
                break
        if "=" in token and token.startswith("-"):
            token = token.split("=", 1)[1]
        if token.startswith("-"):
            continue
        if _looks_like_path(token):
            candidates.append(token)
    return candidates


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

    def is_within_read(self, path: str | Path, *, base: Path | None = None) -> bool:
        resolved = self._candidate(path, base)
        return any(_is_relative_to(resolved, root) for root in self.read_roots)

    def is_within_write(self, path: str | Path, *, base: Path | None = None) -> bool:
        resolved = self._candidate(path, base)
        return any(_is_relative_to(resolved, root) for root in self.write_roots)

    def validate_argv_paths(self, args: list[str], *, cwd: Path) -> None:
        """Reject path-like argv entries that escape the writable roots.

        This is a heuristic: raw tools can still write through mechanisms the
        server cannot see. It catches the common absolute-path and ``..``
        escapes while leaving ordinary relative filenames inside ``cwd`` alone.
        """

        offenders: list[str] = []
        for token in _argv_path_candidates(args):
            candidates = [token]
            if any(ch.isspace() for ch in token):
                try:
                    candidates.extend(part for part in shlex.split(token) if _looks_like_path(part))
                except ValueError:
                    candidates.extend(part for part in token.split() if _looks_like_path(part))
            for candidate in candidates:
                try:
                    self.resolve_write(candidate, base=cwd)
                except PathNotAllowed:
                    if candidate not in offenders:
                        offenders.append(candidate)
        if offenders:
            raise PathNotAllowed(
                f"Raw tool argument(s) point outside the writable workspace: {', '.join(offenders)}",
                hint="Pass workspace-relative paths, or enable a broader write root deliberately.",
                details={"offenders": offenders},
            )

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
