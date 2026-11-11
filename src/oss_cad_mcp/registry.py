"""Catalog of OSS CAD Suite binaries with curated metadata."""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

from oss_cad_mcp.errors import ToolNotFound
from oss_cad_mcp.models.common import Risk, ToolHelp, ToolInfo
from oss_cad_mcp.suite import Suite

_FAMILY_BY_PREFIX = {
    "nextpnr": "pnr",
    "ice": "pnr",
    "ecp": "pnr",
    "gowin": "pnr",
    "prjoxide": "pnr",
    "gm": "pnr",
    "aig": "formal",
    "avy": "formal",
    "btor": "formal",
    "yices": "formal",
    "cvc": "formal",
    "z3": "formal",
    "boolector": "formal",
    "bitwuzla": "formal",
    "pono": "formal",
    "ric3": "formal",
    "suprove": "formal",
    "vcd": "waveform",
    "fst": "waveform",
    "lxt": "waveform",
    "vzt": "waveform",
    "evcd": "waveform",
    "gtkwave": "waveform",
    "surfer": "waveform",
    "surver": "waveform",
    "twinwave": "waveform",
}


def _family_default(name: str, suite: Suite) -> str:
    if suite.is_hardware(name):
        return "hardware"
    if suite.is_gui(name):
        return "gui"
    if suite.is_internal(name):
        return "internal"
    for prefix, family in _FAMILY_BY_PREFIX.items():
        if name.startswith(prefix):
            return family
    return "utility"


def _risk_default(family: str, name: str, suite: Suite) -> Risk:
    if suite.is_hardware(name):
        return "hardware"
    if suite.is_gui(name):
        return "gui"
    if family in {"synthesis", "simulation", "formal"}:
        return "executes_code"
    if family in {"pnr", "waveform"}:
        return "writes_files"
    return "writes_files"


def _recommended_default(family: str) -> str:
    return {
        "synthesis": "cad_synthesize",
        "simulation": "cad_simulate",
        "lint": "cad_check",
        "formal": "cad_verify",
        "pnr": "cad_fpga_build",
        "timing": "cad_timing",
        "waveform": "cad_waveform",
    }.get(family, "cad_tool_run")


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    binary: str
    family: str
    summary: str
    risk: Risk
    agent_facing: bool = True
    gui: bool = False
    hardware: bool = False
    internal: bool = False
    tags: tuple[str, ...] = ()
    help_args: tuple[str, ...] = ("--help", "-h")
    version_args: tuple[str, ...] = ("--version",)
    recommended_tool: str | None = None
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_info(self, *, version: str | None = None) -> ToolInfo:
        return ToolInfo(
            name=self.name,
            binary=self.binary,
            family=self.family,
            summary=self.summary,
            risk=self.risk,
            agent_facing=self.agent_facing,
            gui=self.gui,
            hardware=self.hardware,
            internal=self.internal,
            tags=list(self.tags),
            version=version,
            help_uri=f"cad://help/{self.name}",
            recommended_tool=self.recommended_tool,
        )


class Catalog:
    """Runtime catalog built from the suite bin directory plus curated overrides."""

    def __init__(self, suite: Suite, *, cache_dir: Path) -> None:
        self.suite = suite
        self.cache_dir = cache_dir
        self._help_dir = cache_dir / "help" / suite.version
        self._entries: dict[str, CatalogEntry] = {}
        self._versions: dict[str, str | None] = {}
        self._load()

    def _load(self) -> None:
        overrides: dict[str, dict[str, object]] = {}
        try:
            text = resources.files("oss_cad_mcp.data").joinpath("catalog.toml").read_text(encoding="utf-8")
            raw = tomllib.loads(text)
            overrides = raw.get("tools", {}) if isinstance(raw, dict) else {}
        except FileNotFoundError, OSError, tomllib.TOMLDecodeError:
            overrides = {}

        for name in self.suite.tool_names():
            data = dict(overrides[name]) if name in overrides else {}
            family = str(data.get("family") or _family_default(name, self.suite))
            gui = bool(data.get("gui", self.suite.is_gui(name)))
            hardware = bool(data.get("hardware", self.suite.is_hardware(name)))
            internal = bool(data.get("internal", self.suite.is_internal(name)))
            agent_facing = bool(data.get("agent_facing", not (gui or hardware or internal)))
            risk = data.get("risk") or _risk_default(family, name, self.suite)
            summary = str(data.get("summary") or f"OSS CAD Suite tool: {name}.")
            tags = cast(list[Any], data.get("tags", []))
            help_args = cast(list[Any], data.get("help_args", ["--help", "-h"]))
            version_args = cast(list[Any], data.get("version_args", ["--version"]))
            examples = cast(list[Any], data.get("examples", []))
            notes = cast(list[Any], data.get("notes", []))
            entry = CatalogEntry(
                name=name,
                binary=str(data.get("binary") or name),
                family=family,
                summary=summary,
                risk=risk,  # type: ignore[arg-type]
                agent_facing=agent_facing,
                gui=gui,
                hardware=hardware,
                internal=internal,
                tags=tuple(str(tag) for tag in tags),
                help_args=tuple(str(arg) for arg in help_args),
                version_args=tuple(str(arg) for arg in version_args),
                recommended_tool=(
                    str(data["recommended_tool"]) if data.get("recommended_tool") else _recommended_default(family)
                ),
                examples=tuple(str(example) for example in examples),
                notes=tuple(str(note) for note in notes),
            )
            self._entries[name] = entry

    def get(self, name: str) -> CatalogEntry:
        entry = self._entries.get(name)
        if entry is None:
            raise ToolNotFound(
                f"Unknown suite tool: {name}",
                hint="Use cad_tool_search to discover available tools.",
            )
        return entry

    def names(self) -> list[str]:
        return sorted(self._entries)

    def count(self) -> int:
        return len(self._entries)

    def agent_facing_count(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.agent_facing)

    def families(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._entries.values():
            counts[entry.family] = counts.get(entry.family, 0) + 1
        return dict(sorted(counts.items()))

    def search(
        self,
        query: str = "",
        *,
        family: str | None = None,
        risk: str | None = None,
        include_internal: bool = False,
        include_hardware: bool = False,
        limit: int = 20,
    ) -> list[CatalogEntry]:
        tokens = [token for token in re.split(r"[^a-z0-9_+-]+", query.lower()) if token]
        scored: list[tuple[int, CatalogEntry]] = []
        for entry in self._entries.values():
            if family and entry.family != family:
                continue
            if risk and entry.risk != risk:
                continue
            if entry.internal and not include_internal:
                continue
            if entry.hardware and not include_hardware:
                continue
            haystack = " ".join((entry.name, entry.binary, entry.summary, *entry.tags)).lower()
            score = 0
            if not tokens:
                score = 1 if entry.agent_facing else 0
            else:
                lowered = entry.name.lower()
                for token in tokens:
                    if lowered == token:
                        score += 100
                    elif lowered.startswith(token):
                        score += 50
                    elif token in lowered:
                        score += 25
                    if token in entry.summary.lower():
                        score += 8
                    if any(token in tag.lower() for tag in entry.tags):
                        score += 12
                    if token in haystack:
                        score += 2
            if score:
                if entry.agent_facing:
                    score += 1
                scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [entry for _, entry in scored[:limit]]

    def infos(self, entries: list[CatalogEntry]) -> list[ToolInfo]:
        return [entry.to_info(version=self._versions.get(entry.name)) for entry in entries]

    def version(self, name: str) -> str | None:
        if name in self._versions:
            return self._versions[name]
        entry = self.get(name)
        version = self.suite.tool_version(entry.binary, entry.version_args)
        self._versions[name] = version
        return version

    async def help(self, name: str, *, max_chars: int = 30_000) -> ToolHelp:
        entry = self.get(name)
        help_text: str | None = None
        notes = list(entry.notes)
        cache_path = self._help_dir / f"{self._safe_name(name)}.txt"
        if cache_path.exists():
            try:
                help_text = cache_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                help_text = None
        if help_text is None:
            try:
                help_text = await asyncio.to_thread(self.suite.tool_help, entry.binary, entry.help_args)
            except Exception as exc:  # help is best effort
                notes.append(f"Help command failed: {exc}")
                help_text = ""
            if help_text:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(help_text, encoding="utf-8")
        version = await asyncio.to_thread(self.version, name)
        usage = None
        for line in (help_text or "").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("usage:"):
                usage = stripped
                break
        if usage is None and help_text:
            usage = next((line.strip() for line in help_text.splitlines() if line.strip()), None)
        if entry.hardware:
            notes.append("Hardware tools are catalog-only in v0.1; programming is disabled.")
        if entry.gui:
            notes.append("GUI tool: the server does not run this as a blocking analysis tool.")
        if entry.internal:
            notes.append("Internal runtime helper; not exposed as a first-class agent tool.")
        if not help_text:
            notes.append("No help text was available; showing curated metadata.")
        return ToolHelp(
            tool=entry.name,
            summary=entry.summary,
            binary=entry.binary,
            version=version,
            usage=usage,
            help_text=(help_text or "")[:max_chars] or None,
            examples=list(entry.examples),
            risk=entry.risk,
            recommended_tool=entry.recommended_tool,
            notes=notes,
        )

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)

    def dump_json(self) -> str:
        payload = [
            entry.to_info(version=self._versions.get(entry.name)).model_dump(mode="json")
            for entry in sorted(self._entries.values(), key=lambda item: item.name)
        ]
        return json.dumps(payload, indent=2)

    def load_version_cache(self) -> None:
        path = self.cache_dir / "versions" / f"{self.suite.version}.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return
        if isinstance(data, dict):
            self._versions.update({str(key): (str(value) if value else None) for key, value in data.items()})

    def save_version_cache(self) -> None:
        path = self.cache_dir / "versions" / f"{self.suite.version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._versions, indent=2), encoding="utf-8")
