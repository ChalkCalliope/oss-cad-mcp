"""Locate and describe an OSS CAD Suite installation."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from oss_cad_mcp.errors import SuiteNotFound, ToolNotFound

_MARKERS = ("bin", "VERSION")
_GUI_TOOLS = {
    "gtkwave",
    "surfer",
    "sby-gui",
    "mcy-gui",
    "mcy-dash",
    "xdot",
}
_HARDWARE_TOOLS = {
    "openFPGALoader",
    "openocd",
    "dfu-util",
    "dfu-prefix",
    "dfu-suffix",
    "ecpprog",
    "ecpdap",
    "fujprog",
    "iceprog",
    "icesprog",
    "iceprogduino",
    "lsftdi",
    "lsusb",
}
_VERSION_RE = re.compile(r"\b\d+\.\d+(?:[.\-+~][0-9A-Za-z.]+)?\b")
_BAD_VERSION_PREFIXES = (
    "usage:",
    "warning",
    "error",
    "unknown option",
    "traceback",
    "name ",
    "name:",
    "command line",
)


def _valid_version_line(text: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith(_BAD_VERSION_PREFIXES):
            continue
        if _VERSION_RE.search(line):
            return line[:300]
    return None


_INTERNAL_TOOLS = {
    "bwrap",
    "find_libpython",
    "ghdl1-llvm",
    "imctk-eqy-engine",
    "tabbyadm",
    "tabbypip",
    "tabbypy3",
    "verilator_bin",
    "verilator_bin_dbg",
    "verilator_coverage_bin",
    "verilator_coverage_bin_dbg",
    "yosys-abc",
    "yosys-config",
    "yosys-filterlib",
}


@dataclass(frozen=True)
class HostInfo:
    system: str
    release: str
    machine: str
    wsl: bool
    display: bool
    cxx: str | None
    make: str | None
    git: str | None


def _read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    path = Path("/etc/os-release")
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    return data


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    proc_version = Path("/proc/version")
    if proc_version.exists():
        try:
            return "microsoft" in proc_version.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            pass
    return False


def host_info() -> HostInfo:
    os_release = _read_os_release()
    return HostInfo(
        system=platform.system(),
        release=os_release.get("PRETTY_NAME", platform.platform()),
        machine=platform.machine(),
        wsl=is_wsl(),
        display=bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        cxx=shutil.which("g++") or shutil.which("clang++") or shutil.which("c++"),
        make=shutil.which("make"),
        git=shutil.which("git"),
    )


def _candidate_roots(explicit: Path | str | None) -> Iterable[Path]:
    if explicit:
        yield Path(explicit).expanduser()
    env_root = os.environ.get("OSS_CAD_SUITE_ROOT")
    if env_root:
        yield Path(env_root).expanduser()
    yield Path.home() / "Kits" / "oss-cad-suite"
    yield Path.home() / "oss-cad-suite"
    yield Path("/opt/oss-cad-suite")

    yosys = shutil.which("yosys")
    if yosys:
        path = Path(yosys).resolve()
        if path.parent.name == "bin":
            yield path.parent.parent


def _validate_root(root: Path) -> bool:
    return all((root / marker).exists() for marker in _MARKERS) and (root / "bin").is_dir()


@dataclass(frozen=True)
class Suite:
    """A discovered OSS CAD Suite installation."""

    root: Path
    version: str
    host: HostInfo = field(default_factory=host_info)

    @classmethod
    def discover(cls, explicit: Path | str | None = None) -> Suite:
        tried: list[str] = []
        for candidate in _candidate_roots(explicit):
            try:
                root = candidate.resolve()
            except OSError:
                continue
            tried.append(str(root))
            if _validate_root(root):
                version_path = root / "VERSION"
                try:
                    version = version_path.read_text(encoding="utf-8").strip()
                except OSError:
                    version = "unknown"
                return cls(root=root, version=version)
        hint = (
            "Set OSS_CAD_SUITE_ROOT to the extracted oss-cad-suite directory, "
            "or pass --suite-root. Expected markers: bin/yosys, VERSION, share/yosys."
        )
        raise SuiteNotFound(
            "Could not find an OSS CAD Suite installation.",
            hint=hint,
            details={"tried": tried},
        )

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def py3bin_dir(self) -> Path:
        return self.root / "py3bin"

    @property
    def platform_label(self) -> str:
        if self.host.wsl:
            return f"{self.host.release} (WSL2)"
        return self.host.release

    @property
    def verified_platform(self) -> bool:
        return self.host.wsl and self.host.machine in {"x86_64", "AMD64"}

    def binary(self, name: str) -> Path:
        candidates = [self.bin_dir / name]
        if os.name == "nt":
            candidates.extend([self.bin_dir / f"{name}.exe", self.bin_dir / f"{name}.bat"])
        for path in candidates:
            if path.is_file():
                return path
        raise ToolNotFound(
            f"Tool {name!r} is not installed in this OSS CAD Suite.",
            hint="Run cad_tool_search to see available tools, or reinstall the suite.",
        )

    def has(self, name: str) -> bool:
        try:
            self.binary(name)
        except ToolNotFound:
            return False
        return True

    def tool_names(self) -> list[str]:
        if not self.bin_dir.is_dir():
            return []
        names: list[str] = []
        for path in self.bin_dir.iterdir():
            if path.is_dir() or path.name.startswith("."):
                continue
            if path.is_file():
                names.append(path.name)
        return sorted(set(names))

    def run_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT"):
            env.pop(key, None)
        path_parts = [str(self.bin_dir), str(self.py3bin_dir)]
        existing = env.get("PATH", "")
        if existing:
            path_parts.append(existing)
        env["PATH"] = os.pathsep.join(path_parts)
        env["VERILATOR_ROOT"] = str(self.root / "share" / "verilator")
        env["GHDL_PREFIX"] = str(self.root / "lib" / "ghdl")
        env["OSS_CAD_SUITE_ROOT"] = str(self.root)
        env["NO_COLOR"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("TERM", "dumb")
        if extra:
            env.update(extra)
        return env

    def run_text(
        self,
        name: str,
        args: list[str] | None = None,
        *,
        timeout: float = 8.0,
        cwd: Path | None = None,
    ) -> tuple[int, str]:
        binary = self.binary(name)
        try:
            proc = subprocess.run(
                [str(binary), *(args or [])],
                cwd=str(cwd) if cwd else None,
                env=self.run_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 124, str(exc)
        return proc.returncode, proc.stdout or ""

    def tool_version(self, name: str, version_args: tuple[str, ...] = ("--version",)) -> str | None:
        if not self.has(name):
            return None
        for args in (version_args, ("-V",), ("-v",), ("version",)):
            code, text = self.run_text(name, list(args), timeout=5.0)
            if code != 0:
                continue
            line = _valid_version_line(text)
            if line:
                return line
        return None

    def tool_help(self, name: str, help_args: tuple[str, ...] = ("--help", "-h", "help")) -> str:
        if not self.has(name):
            raise ToolNotFound(f"Tool {name!r} is not installed in this OSS CAD Suite.")
        best = ""
        for arg in help_args:
            code, text = self.run_text(name, [arg], timeout=8.0)
            if len(text) > len(best):
                best = text
            if code == 0 and text.strip():
                break
        return best.strip()

    def is_gui(self, name: str) -> bool:
        return name in _GUI_TOOLS

    def is_hardware(self, name: str) -> bool:
        return name in _HARDWARE_TOOLS

    def is_internal(self, name: str) -> bool:
        return name in _INTERNAL_TOOLS

    def sandbox_available(self) -> tuple[bool, str | None]:
        bwrap = self.root / "bin" / "bwrap"
        if not bwrap.is_file():
            return False, "bwrap is not present in this suite."
        try:
            proc = subprocess.run(
                [str(bwrap), "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
                env=self.run_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=5.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        if proc.returncode == 0:
            return True, None
        return False, (proc.stdout or "").strip() or f"bwrap exited {proc.returncode}"
