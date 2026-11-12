"""Project discovery, configuration, and source-set resolution."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from oss_cad_mcp.config import ProjectConfigError
from oss_cad_mcp.errors import InvalidInput
from oss_cad_mcp.exec.paths import PathPolicy
from oss_cad_mcp.models.common import SourceSet
from oss_cad_mcp.models.project import (
    ConstraintFile,
    ProjectConfig,
    ProjectProfile,
)

_HDL_SUFFIXES = {
    ".v": "verilog",
    ".vh": "verilog",
    ".sv": "systemverilog",
    ".svh": "systemverilog",
    ".vhd": "vhdl",
    ".vhdl": "vhdl",
}
_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "build",
    "obj_dir",
    ".oss-cad-mcp",
    "third-party",
    "node_modules",
    "__pycache__",
}
_CONSTRAINT_KINDS = {
    ".pcf": ("pcf", "ice40"),
    ".lpf": ("lpf", "ecp5"),
    ".cst": ("cst", "gowin"),
    ".sdc": ("sdc", None),
    ".xdc": ("xdc", None),
    ".qsf": ("qsf", None),
}
_CONFIG_NAMES = ("oss-cad-project.toml", ".oss-cad-project.toml")
_FORMAL_DIRS = {"formal", "verif", "verification", "proof", "sby"}


def _excluded_path(path: Path, root: Path, excluded: Iterable[str]) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(part in excluded for part in relative.parts)


def find_config_file(root: Path) -> Path | None:
    for name in _CONFIG_NAMES:
        path = root / name
        if path.is_file():
            return path
    return None


def load_project_config(root: Path) -> tuple[ProjectConfig | None, Path | None]:
    path = find_config_file(root)
    if path is None:
        return None, None
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectConfigError(f"Could not read {path}: {exc}") from exc
    project = raw.get("project", {}) if isinstance(raw, dict) else {}
    target = raw.get("target", {}) if isinstance(raw, dict) else {}
    sim = raw.get("sim", {}) if isinstance(raw, dict) else {}
    formal = raw.get("formal", {}) if isinstance(raw, dict) else {}
    data = {
        **project,
        "target": target,
        "sim": sim,
        "formal": formal,
    }
    return ProjectConfig.model_validate(data), path


def _iter_hdl_files(root: Path, exclude: Iterable[str] = ()) -> list[Path]:
    excluded = _EXCLUDE_DIRS | {item.strip("/") for item in exclude if item.strip()}
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _HDL_SUFFIXES:
            continue
        if _excluded_path(path, root, excluded):
            continue
        files.append(path)
    return sorted(files)


def _module_names(text: str) -> list[str]:
    names = re.findall(r"\b(?:module|macromodule)\s+(?:automatic\s+)?([A-Za-z_]\w*)", text)
    return list(dict.fromkeys(names))


def _entity_names(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\bentity\s+([A-Za-z_]\w*)\s+is\b", text, re.IGNORECASE)))


def _instantiated_names(text: str, module_names: set[str]) -> set[str]:
    found: set[str] = set()
    for name in module_names:
        pattern = re.compile(
            rf"\b{re.escape(name)}\s+(?:#\s*\([^;]*?\)\s*)?([A-Za-z_]\w*)\s*\(",
            re.DOTALL,
        )
        if pattern.search(text):
            found.add(name)
    return found


def _instantiated_entities(text: str, entity_names: set[str]) -> set[str]:
    """Find VHDL direct/component instantiations of project entities."""
    found: set[str] = set()
    for name in entity_names:
        escaped = re.escape(name)
        direct = rf":\s*entity\s+(?:work\.)?{escaped}\b"
        component = rf"\bcomponent\s+{escaped}\b"
        labelled = rf"\b[A-Za-z_]\w*\s*:\s*(?:work\.)?{escaped}\b"
        if (
            re.search(direct, text, re.IGNORECASE)
            or re.search(component, text, re.IGNORECASE)
            or re.search(labelled, text, re.IGNORECASE)
        ):
            found.add(name)
    return found


def _testbench_like(name: str, path: Path) -> bool:
    lowered = name.lower()
    return (
        lowered.startswith("tb_")
        or lowered.endswith("_tb")
        or "testbench" in lowered
        or "tb" in {part.lower() for part in path.parts}
        or "test" in {part.lower() for part in path.parts}
    )


def _formal_like(name: str, path: Path) -> bool:
    lowered = name.lower()
    return (
        lowered.endswith("_formal")
        or lowered.endswith("_fv")
        or any(part.lower() in _FORMAL_DIRS for part in path.parts)
    )


def unit_instantiates(path: Path, unit: str) -> bool:
    """Return True when the HDL file at *path* instantiates *unit*."""

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return unit in (_instantiated_names(text, {unit}) | _instantiated_entities(text, {unit}))


def _clocks(text: str) -> list[str]:
    clocks = re.findall(r"\b(?:posedge|negedge)\s+([A-Za-z_]\w*)", text)
    clocks.extend(re.findall(r"\balways_ff\s*@\s*\(\s*(?:posedge|negedge)\s+([A-Za-z_]\w*)", text))
    clocks.extend(
        re.findall(
            r"\b(?:rising_edge|falling_edge)\s*\(\s*([A-Za-z_]\w*)\s*\)",
            text,
            re.IGNORECASE,
        )
    )
    return list(dict.fromkeys(clocks))


def inspect_project(
    root: Path,
    *,
    paths: PathPolicy,
    deep: bool = False,
) -> ProjectProfile:
    root = paths.resolve_read(root, must_exist=True)
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    config, config_path = load_project_config(root)
    files = _iter_hdl_files(root, config.exclude if config else ())
    modules: dict[str, Path] = {}
    entities: dict[str, Path] = {}
    text_by_file: dict[Path, str] = {}
    languages: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text_by_file[path] = text
        languages.add(_HDL_SUFFIXES[path.suffix.lower()])
        for name in _module_names(text):
            modules.setdefault(name, path)
        for name in _entity_names(text):
            entities.setdefault(name, path)

    module_names = set(modules)
    entity_names = set(entities)
    units: dict[str, Path] = {**modules, **entities}
    testbenches = {
        name for name, path in units.items() if _testbench_like(name, path) or _testbench_like(path.stem, path)
    }
    formal_units = {name for name, path in units.items() if _formal_like(name, path) or _formal_like(path.stem, path)}
    testbench_paths = {units[name] for name in testbenches}
    formal_paths = {units[name] for name in formal_units}

    def _instantiated(text: str) -> set[str]:
        return _instantiated_names(text, module_names) | _instantiated_entities(text, entity_names)

    instantiated_in_sources: set[str] = set()
    for path, text in text_by_file.items():
        if path in testbench_paths or path in formal_paths:
            continue
        instantiated_in_sources |= _instantiated(text)

    instantiated_anywhere: set[str] = set()
    for _path, text in text_by_file.items():
        instantiated_anywhere |= _instantiated(text)

    candidates = [
        name
        for name in units
        if name not in instantiated_in_sources and name not in testbenches and name not in formal_units
    ]
    if not candidates:
        candidates = [
            name
            for name in units
            if name not in instantiated_anywhere and name not in testbenches and name not in formal_units
        ]
    if not candidates:
        candidates = [name for name in units if name not in testbenches and name not in formal_units]

    def _child_count(name: str) -> int:
        path = units.get(name)
        text = text_by_file.get(path) if path else ""
        return len(_instantiated(text) & set(units)) if text else 0

    candidates = sorted(candidates, key=lambda name: (-_child_count(name), name))
    if config and config.top and config.top in units:
        candidates = [config.top, *(name for name in candidates if name != config.top)]
    top_candidates = list(dict.fromkeys(candidates))
    source_files = [
        paths.display(path)
        for path in files
        if path not in testbench_paths and path not in formal_paths and path.suffix.lower() not in {".vh", ".svh"}
    ]
    testbench_files = [paths.display(path) for path in files if path in testbench_paths]
    formal_files = [paths.display(path) for path in files if path in formal_paths]

    constraints: list[ConstraintFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _excluded_path(path, root, _EXCLUDE_DIRS):
            continue
        kind_target = _CONSTRAINT_KINDS.get(path.suffix.lower())
        if kind_target:
            kind, target = kind_target
            constraints.append(
                ConstraintFile(path=paths.display(path), kind=kind, target=target)  # type: ignore[arg-type]
            )

    build_files = [
        paths.display(path)
        for path in sorted(root.iterdir())
        if path.is_file()
        and (
            path.name in {"Makefile", "makefile", "CMakeLists.txt"}
            or path.suffix.lower() in {".sby", ".eqy", ".mcy", ".f"}
        )
    ]

    source_text = "\n".join(
        text for path, text in text_by_file.items() if path not in testbench_paths and path not in formal_paths
    )
    clocks = _clocks(source_text)
    warnings: list[str] = []
    if not files:
        warnings.append("No HDL files were found under the project root.")
    if config is None:
        warnings.append("No oss-cad-project.toml was found; using automatic detection.")
    if files and not top_candidates:
        warnings.append("No top-level HDL unit was detected.")
    known_units = set(modules) | set(entities)
    if top_candidates and config and config.top and config.top not in known_units:
        warnings.append(f"Configured top {config.top!r} was not found in the project.")
    if any(path.as_posix().startswith("/mnt/") for path in files):
        warnings.append("Project appears to be on a mounted Windows filesystem; prefer the Linux filesystem.")

    return ProjectProfile(
        root=str(root),
        config_file=paths.display(config_path) if config_path else None,
        config=config,
        hdl_files=[paths.display(path) for path in files],
        source_files=source_files,
        testbench_files=testbench_files,
        formal_files=formal_files,
        languages=sorted(languages),
        top_candidates=top_candidates,
        testbenches=sorted(set(testbenches)),
        testbench_units={name: paths.display(path) for name, path in units.items() if name in testbenches},
        formal_units=sorted(formal_units),
        modules=sorted(modules),
        entities=sorted(entities),
        constraints=constraints,
        clocks=clocks,
        build_files=build_files,
        board=config.target.board if config else None,
        target=config.target.family if config else None,
        warnings=warnings,
    )


@dataclass
class ResolvedSources:
    files: list[Path] = field(default_factory=list)
    include_dirs: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    top: str | None = None
    language: str = "systemverilog"
    std: str = "1800-2017"
    warnings: list[str] = field(default_factory=list)


def parse_filelist(
    path: Path,
    *,
    paths: PathPolicy,
    missing: list[Path] | None = None,
    seen: set[Path] | None = None,
    depth: int = 0,
) -> tuple[list[Path], list[Path], dict[str, str]]:
    if depth > 32:
        raise InvalidInput(
            f"Filelist nesting is too deep: {path}",
            hint="Flatten the -f includes or remove a recursive filelist reference.",
        )
    resolved_path = path.resolve()
    visited = seen if seen is not None else set()
    if resolved_path in visited:
        raise InvalidInput(
            f"Recursive filelist reference: {path}",
            hint="Remove the self-referential -f entry.",
        )
    visited.add(resolved_path)
    files: list[Path] = []
    includes: list[Path] = []
    defines: dict[str, str] = {}
    base = path.parent
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("+incdir+"):
            for item in line[len("+incdir+") :].split("+"):
                if item:
                    includes.append(paths.resolve_read(item, base=base, must_exist=False))
            continue
        if line.startswith("+define+"):
            body = line[len("+define+") :]
            for item in body.split("+"):
                if not item:
                    continue
                name, _, value = item.partition("=")
                defines[name] = value or "1"
            continue
        if line.startswith("-I"):
            includes.append(paths.resolve_read(line[2:].strip(), base=base, must_exist=False))
            continue
        if line.startswith("-D"):
            body = line[2:].strip()
            name, _, value = body.partition("=")
            defines[name] = value or "1"
            continue
        if line.startswith("-f "):
            nested = paths.resolve_read(line[3:].strip(), base=base, must_exist=False)
            if nested.is_file():
                n_files, n_includes, n_defines = parse_filelist(
                    nested, paths=paths, missing=missing, seen=visited, depth=depth + 1
                )
                files.extend(n_files)
                includes.extend(n_includes)
                defines.update(n_defines)
            elif missing is not None:
                missing.append(nested)
            continue
        if line.startswith(("+", "-")):
            # Unsupported filelist options (for example +libext+ or -y) are
            # ignored rather than being mistaken for missing source files.
            continue
        candidate = paths.resolve_read(line, base=base, must_exist=False)
        if candidate.is_file():
            files.append(candidate)
        elif missing is not None:
            missing.append(candidate)
    return files, includes, defines


def resolve_sources(
    source: SourceSet | None,
    *,
    project: ProjectConfig | None,
    paths: PathPolicy,
    include_testbenches: bool = False,
) -> ResolvedSources:
    source = source or SourceSet()
    result = ResolvedSources()
    result.defines.update(project.defines if project else {})
    result.defines.update(source.defines)
    result.include_dirs.extend(
        paths.resolve_read(item, must_exist=False) for item in (project.include_dirs if project else [])
    )
    result.include_dirs.extend(paths.resolve_read(item, must_exist=False) for item in source.include_dirs)
    if source.filelist:
        filelist = paths.resolve_read(source.filelist, must_exist=True)
        missing: list[Path] = []
        files, includes, defines = parse_filelist(filelist, paths=paths, missing=missing)
        if missing:
            names = ", ".join(str(path) for path in missing[:10])
            suffix = f" (and {len(missing) - 10} more)" if len(missing) > 10 else ""
            raise InvalidInput(
                f"Filelist references missing file(s): {names}{suffix}",
                hint="Fix or remove the missing entries in the filelist.",
                details={"missing": [str(path) for path in missing]},
            )
        result.files.extend(files)
        result.include_dirs.extend(includes)
        result.defines.update(defines)

    patterns = list(source.files)
    if not patterns and not source.filelist:
        if project and project.sources:
            patterns.extend(project.sources)
        if include_testbenches and project and project.testbenches:
            patterns.extend(project.testbenches)
        if not patterns:
            profile = inspect_project(paths.workspace, paths=paths)
            patterns = profile.hdl_files
        if not patterns:
            return result
    for pattern in patterns:
        matches = paths.glob(pattern, must_exist=True)
        result.files.extend(matches)

    result.files = sorted(dict.fromkeys(result.files))
    result.include_dirs = list(dict.fromkeys(result.include_dirs))
    result.top = source.top or (project.top if project else None)
    if not result.top and include_testbenches and project and project.sim.testbench:
        result.top = project.sim.testbench

    suffix_languages = {_HDL_SUFFIXES.get(path.suffix.lower(), "verilog") for path in result.files}
    if source.language != "auto":
        result.language = source.language
    elif suffix_languages == {"vhdl"}:
        result.language = "vhdl"
    elif "systemverilog" in suffix_languages or any(path.suffix.lower() == ".sv" for path in result.files):
        result.language = "systemverilog"
    elif suffix_languages == {"verilog"}:
        result.language = "verilog"
    else:
        result.language = "mixed"

    result.std = (
        source.std
        or (project.std if project else None)
        or (
            "08"
            if result.language == "vhdl"
            else "1800-2017"
            if result.language in {"systemverilog", "mixed"}
            else "1364-2005"
        )
    )
    return result
