"""Download and install daily OSS CAD Suite releases.

This module intentionally uses only the Python standard library so it can run
before the MCP server's virtual environment is created. The standalone script
``scripts/update_suite.py`` imports it, and the MCP CLI exposes it as
``oss-cad-mcp update-suite``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

GITHUB_REPO = "YosysHQ/oss-cad-suite-build"
GITHUB_BASE = "https://github.com"
USER_AGENT = "oss-cad-mcp-updater/0.1"
ASSET_RE = re.compile(r"^oss-cad-suite-(?P<platform>[a-z0-9-]+)-(?P<version>\d{8})\.(?P<ext>tgz|zip)$")
DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})")
SIZE_RE = re.compile(r">(\d+(?:\.\d+)?)\s*(KB|MB|GB)<")
_TAG_RE = re.compile(r"/releases/tag/([^/?#]+)")
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


class UpdateError(RuntimeError):
    """An actionable updater failure."""


@dataclass(frozen=True)
class PlatformTarget:
    key: str
    system: str
    machine: str


@dataclass(frozen=True)
class ReleaseAsset:
    tag: str
    name: str
    platform: str
    version: str
    url: str
    sha256: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class UpdateOptions:
    path: Path | None = None
    check: bool = False
    force: bool = False
    keep_backup: bool = True
    resume: bool = True
    release_tag: str | None = None
    validate_runtime: bool = True
    timeout_s: float = 60.0


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    target: Path | None = None
    version: str | None = None
    asset: ReleaseAsset | None = None
    backup: Path | None = None
    message: str = ""


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def detect_platform(system: str | None = None, machine: str | None = None) -> PlatformTarget:
    """Map the current host to the release asset platform key."""

    system_name = (system or platform.system()).lower()
    machine_name = (machine or platform.machine()).lower()
    if system_name == "linux":
        if machine_name in {"x86_64", "amd64"}:
            return PlatformTarget("linux-x64", system_name, machine_name)
        if machine_name in {"aarch64", "arm64"}:
            return PlatformTarget("linux-arm64", system_name, machine_name)
    elif system_name == "darwin":
        if machine_name in {"arm64", "aarch64"}:
            return PlatformTarget("darwin-arm64", system_name, machine_name)
        if machine_name in {"x86_64", "amd64"}:
            return PlatformTarget("darwin-x64", system_name, machine_name)
    elif system_name == "windows":
        raise UpdateError("Native Windows is not supported by this project. Use WSL2 and the linux-x64 asset.")
    raise UpdateError(f"Unsupported platform for OSS CAD Suite updates: {system_name}/{machine_name}")


def tag_to_version(tag: str) -> str:
    stripped = tag.strip().removeprefix("v")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return stripped.replace("-", "")
    if re.fullmatch(r"\d{8}", stripped):
        return stripped
    return stripped.replace("-", "")


def version_is_newer(candidate: str, current: str | None) -> bool:
    if current is None:
        return True
    return candidate > current


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Any:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def latest_release_tag(timeout: float = 30.0) -> str:
    """Find the latest release tag without using the rate-limited JSON API."""

    url = f"{GITHUB_BASE}/{GITHUB_REPO}/releases/latest"
    try:
        response = _http_request(url, method="HEAD", timeout=timeout)
        final_url = response.geturl()
        response.close()
        match = _TAG_RE.search(final_url)
        if match:
            return urllib.parse.unquote(match.group(1))
    except urllib.error.URLError, TimeoutError, OSError:
        pass
    tags = recent_release_tags(limit=1, timeout=timeout)
    if tags:
        return tags[0]
    raise UpdateError("Could not determine the latest OSS CAD Suite release tag.")


def recent_release_tags(limit: int = 10, timeout: float = 30.0) -> list[str]:
    url = f"{GITHUB_BASE}/{GITHUB_REPO}/releases.atom"
    try:
        response = _http_request(url, timeout=timeout)
        data = response.read()
        response.close()
        root = ET.fromstring(data)
    except urllib.error.URLError, TimeoutError, OSError, ET.ParseError:
        return []
    tags: list[str] = []
    for entry in root.findall("a:entry", _ATOM_NS):
        link = entry.find("a:link", _ATOM_NS)
        if link is None:
            continue
        href = link.attrib.get("href", "")
        match = _TAG_RE.search(href)
        if match:
            tag = urllib.parse.unquote(match.group(1))
            if tag not in tags:
                tags.append(tag)
        if len(tags) >= limit:
            break
    return tags


def _parse_size(text: str | None) -> int | None:
    if not text:
        return None
    match = SIZE_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    multiplier = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}[match.group(2)]
    return int(value * multiplier)


def parse_asset_page(html: str, *, tag: str, platform_key: str) -> ReleaseAsset | None:
    """Parse a GitHub expanded-assets page for the requested platform."""

    hrefs = re.findall(r'href="([^"]+/releases/download/[^"]+)"', html)
    for href in hrefs:
        path = urllib.parse.urlparse(href).path
        name = urllib.parse.unquote(PurePosixPath(path).name)
        match = ASSET_RE.match(name)
        if not match or match.group("platform") != platform_key:
            continue
        digest = None
        marker = f'aria-label="Copy to clipboard digest for {name}"'
        marker_index = html.find(marker)
        if marker_index >= 0:
            digest_match = DIGEST_RE.search(html[marker_index : marker_index + 2000])
            if digest_match:
                digest = digest_match.group(1)
        if digest is None:
            after = html.find(href)
            digest_match = DIGEST_RE.search(html[after : after + 6000]) if after >= 0 else None
            if digest_match:
                digest = digest_match.group(1)
        size = None
        after = html.find(href)
        if after >= 0:
            size = _parse_size(html[after : after + 6000])
        return ReleaseAsset(
            tag=tag,
            name=name,
            platform=platform_key,
            version=match.group("version"),
            url=urllib.parse.urljoin(GITHUB_BASE, href),
            sha256=digest,
            size_bytes=size,
        )
    return None


def fetch_release_assets(tag: str, platform_key: str, timeout: float = 30.0) -> ReleaseAsset | None:
    url = f"{GITHUB_BASE}/{GITHUB_REPO}/releases/expanded_assets/{urllib.parse.quote(tag, safe='')}"
    try:
        response = _http_request(url, timeout=timeout)
        html = response.read().decode("utf-8", errors="replace")
        response.close()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Could not read GitHub release assets for {tag}: {exc}") from exc
    return parse_asset_page(html, tag=tag, platform_key=platform_key)


def find_release_asset(
    platform_key: str,
    *,
    release_tag: str | None = None,
    max_releases: int = 10,
    timeout: float = 30.0,
) -> ReleaseAsset:
    if release_tag:
        tags = [release_tag]
    else:
        tags = [latest_release_tag(timeout=timeout)]
        with contextlib.suppress(UpdateError):
            tags.extend(tag for tag in recent_release_tags(limit=max_releases, timeout=timeout) if tag not in tags)
    for tag in tags[:max_releases]:
        asset = fetch_release_assets(tag, platform_key, timeout=timeout)
        if asset is not None:
            return asset
    raise UpdateError(
        f"No OSS CAD Suite asset for platform {platform_key!r} was found in the latest "
        f"{min(len(tags), max_releases)} release(s)."
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _progress(downloaded: int, total: int | None, started: float) -> None:
    elapsed = max(0.001, time.monotonic() - started)
    speed = downloaded / elapsed / (1024 * 1024)
    if total:
        percent = 100.0 * downloaded / total
        log(f"  {percent:5.1f}%  {downloaded / 1024**2:.1f}/{total / 1024**2:.1f} MiB  {speed:.1f} MiB/s")
    else:
        log(f"  {downloaded / 1024**2:.1f} MiB  {speed:.1f} MiB/s")


def download_asset(
    asset: ReleaseAsset,
    destination: Path,
    *,
    resume: bool = True,
    retries: int = 3,
    timeout: float = 60.0,
) -> Path:
    """Download an asset with resumable partial-file support and digest verification."""

    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    if not resume and part.exists():
        part.unlink()
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        start = part.stat().st_size if resume and part.exists() else 0
        headers: dict[str, str] = {}
        if start > 0:
            headers["Range"] = f"bytes={start}-"
        try:
            response = _http_request(asset.url, headers=headers, timeout=timeout)
            status = getattr(response, "status", 200)
            if start > 0 and status != 206:
                start = 0
                part.unlink(missing_ok=True)
            content_length = response.headers.get("Content-Length")
            remaining = int(content_length) if content_length and content_length.isdigit() else None
            total = (start + remaining) if remaining is not None else asset.size_bytes
            mode = "ab" if start > 0 else "wb"
            downloaded = start
            started = time.monotonic()
            last_report = started
            with response, part.open(mode) as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 1.0:
                        _progress(downloaded, total, started)
                        last_report = now
            _progress(downloaded, total or downloaded, started)
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
            last_error = exc
            if attempt >= retries:
                raise UpdateError(f"Download failed after {retries} attempts: {exc}") from exc
            log(f"Download interrupted ({exc}); retrying with partial file ({attempt}/{retries})...")
            time.sleep(min(5.0, 1.5 * attempt))
    if last_error is not None and not part.exists():
        raise UpdateError(f"Download failed: {last_error}")
    actual_digest = _sha256(part)
    if asset.sha256 and actual_digest != asset.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError(f"SHA-256 mismatch for {asset.name}: expected {asset.sha256}, got {actual_digest}.")
    part.replace(destination)
    log(f"Verified SHA-256: {actual_digest}")
    return destination


def _safe_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts:
        raise UpdateError(f"Refusing unsafe archive member: {member.name}")
    if member.isdev():
        raise UpdateError(f"Refusing device archive member: {member.name}")
    if member.issym() or member.islnk():
        link = PurePosixPath(member.linkname)
        if link.is_absolute():
            raise UpdateError(f"Refusing absolute archive link: {member.name} -> {member.linkname}")
        resolved = posixpath.normpath(str(name.parent / link))
        if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
            raise UpdateError(f"Refusing escaping archive link: {member.name} -> {member.linkname}")


def extract_archive(archive: Path, staging: Path) -> Path:
    """Safely extract a release archive and return the suite root inside it."""

    if archive.suffix.lower() != ".tgz" and not archive.name.lower().endswith(".tar.gz"):
        raise UpdateError(f"Unsupported release archive format: {archive.name}")
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            members = tar.getmembers()
            for member in members:
                _safe_member(member)
            tar.extractall(staging, members=members, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError(f"Could not extract {archive.name}: {exc}") from exc
    return find_suite_root(staging)


def find_suite_root(staging: Path) -> Path:
    candidates = [staging, *[path for path in staging.iterdir() if path.is_dir()]]
    for candidate in candidates:
        if (candidate / "bin" / "yosys").is_file() and (candidate / "VERSION").is_file():
            return candidate
    for version_file in staging.rglob("VERSION"):
        candidate = version_file.parent
        if (candidate / "bin" / "yosys").is_file():
            return candidate
    raise UpdateError("The downloaded archive does not contain a valid OSS CAD Suite root.")


def validate_suite_root(
    suite_root: Path,
    *,
    validate_runtime: bool = True,
    timeout: float = 30.0,
) -> None:
    if not (suite_root / "bin" / "yosys").is_file():
        raise UpdateError("New suite root is missing bin/yosys.")
    if not (suite_root / "VERSION").is_file():
        raise UpdateError("New suite root is missing VERSION.")
    if not os.access(suite_root / "bin" / "yosys", os.X_OK):
        raise UpdateError("New suite bin/yosys is not executable.")
    if not validate_runtime:
        return
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(suite_root / "bin"), str(suite_root / "py3bin"), env.get("PATH", "")])
    env["NO_COLOR"] = "1"
    try:
        result = subprocess.run(
            [str(suite_root / "bin" / "yosys"), "-V"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Downloaded suite failed its runtime check: {exc}") from exc
    if result.returncode != 0:
        raise UpdateError(
            f"Downloaded suite failed its runtime check (yosys -V exited {result.returncode}): {result.stdout.strip()}"
        )


def _is_suite_root(path: Path) -> bool:
    return (path / "bin" / "yosys").is_file() and (path / "VERSION").is_file()


def _installed_version(path: Path) -> str | None:
    version_file = path / "VERSION"
    if not version_file.is_file():
        return None
    try:
        value = version_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def normalize_target(path: Path | str) -> Path:
    """Accept a suite root, its bin directory, or a parent containing oss-cad-suite."""

    raw = Path(path).expanduser()
    if raw.exists():
        resolved = raw.resolve()
        if resolved.is_file():
            raise UpdateError(f"Install path is a file, not a suite directory: {resolved}")
        if resolved.name == "bin" and _is_suite_root(resolved.parent):
            return resolved.parent
        if _is_suite_root(resolved):
            return resolved
        nested = resolved / "oss-cad-suite"
        if _is_suite_root(nested):
            return nested
        return resolved
    return raw.resolve()


def detect_installed_suite() -> Path | None:
    env_root = os.environ.get("OSS_CAD_SUITE_ROOT")
    if env_root:
        candidate = normalize_target(env_root)
        if _is_suite_root(candidate):
            return candidate
    yosys = shutil.which("yosys")
    if yosys:
        binary = Path(yosys).resolve()
        if binary.parent.name == "bin" and _is_suite_root(binary.parent.parent):
            return binary.parent.parent
    return None


def _backup_path(target: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = target.with_name(f"{target.name}.backup-{stamp}")
    if not candidate.exists():
        return candidate
    return target.with_name(f"{target.name}.backup-{stamp}-{uuid.uuid4().hex[:6]}")


def install_archive(
    archive: Path,
    target: Path,
    *,
    keep_backup: bool = True,
    force: bool = False,
    validate_runtime: bool = True,
) -> UpdateResult:
    """Extract and atomically swap a downloaded suite archive into target."""

    target = normalize_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.update-{uuid.uuid4().hex[:8]}"
    backup: Path | None = None
    moved_old = False
    try:
        suite_root = extract_archive(archive, staging)
        validate_suite_root(suite_root, validate_runtime=validate_runtime)
        version = _installed_version(suite_root)
        if target.exists():
            if not _is_suite_root(target) and any(target.iterdir()) and not force:
                raise UpdateError(
                    f"Target exists but is not an OSS CAD Suite and is not empty: {target}. Use --force to replace it."
                )
            backup = _backup_path(target)
            target.rename(backup)
            moved_old = True
        suite_root.replace(target)
        if moved_old and backup is not None and not keep_backup:
            shutil.rmtree(backup, ignore_errors=True)
            backup = None
        return UpdateResult(
            ok=True,
            target=target,
            version=version,
            backup=backup,
            message=f"Installed OSS CAD Suite {version or 'unknown'} at {target}.",
        )
    except Exception:
        if moved_old and backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def update_suite(options: UpdateOptions) -> UpdateResult:
    """Check or install the latest release for the current platform."""

    platform_target = detect_platform()
    if options.path is None:
        detected = detect_installed_suite()
        if detected is None:
            raise UpdateError(
                "No install path was given and no existing OSS CAD Suite was detected. "
                "Pass the suite root explicitly, for example: "
                "update_suite.py ~/Kits/oss-cad-suite"
            )
        target = detected
    else:
        target = normalize_target(options.path)
    current_version = _installed_version(target) if target.exists() else None
    if target.exists() and not _is_suite_root(target) and any(target.iterdir()) and not options.force:
        raise UpdateError(
            f"Target exists but is not an OSS CAD Suite and is not empty: {target}. Use --force to replace it."
        )
    asset = find_release_asset(
        platform_target.key,
        release_tag=options.release_tag,
        timeout=options.timeout_s,
    )
    log(f"Latest {platform_target.key} asset: {asset.name} ({asset.tag})")
    if options.check:
        return UpdateResult(
            ok=True,
            target=target,
            version=current_version,
            asset=asset,
            message=(f"Installed: {current_version or 'not installed'}; available: {asset.version} ({asset.name})."),
        )
    if not options.force and current_version is not None and not version_is_newer(asset.version, current_version):
        return UpdateResult(
            ok=True,
            target=target,
            version=current_version,
            asset=asset,
            message=f"OSS CAD Suite is already up to date at {current_version}.",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    destination = target.parent / asset.name
    log(
        f"Downloading {asset.name} ({asset.size_bytes / 1024**2:.1f} MiB)..."
        if asset.size_bytes
        else f"Downloading {asset.name}..."
    )
    download_asset(
        asset,
        destination,
        resume=options.resume,
        timeout=options.timeout_s,
    )
    try:
        result = install_archive(
            destination,
            target,
            keep_backup=options.keep_backup,
            force=options.force,
            validate_runtime=options.validate_runtime,
        )
    finally:
        destination.unlink(missing_ok=True)
    log(result.message)
    if result.backup:
        log(f"Previous installation kept at {result.backup} (remove it when you no longer need it).")
    return UpdateResult(
        ok=result.ok,
        target=result.target,
        version=result.version,
        asset=asset,
        backup=result.backup,
        message=result.message,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and install the latest OSS CAD Suite release.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The install path is the suite root, normally .../oss-cad-suite. If omitted,
OSS_CAD_SUITE_ROOT or a yosys found on PATH is used and replaced in place.

Examples:
  update_suite.py ~/Kits/oss-cad-suite
  update_suite.py --check ~/Kits/oss-cad-suite
  update_suite.py                     # detect an existing installation
  update_suite.py --release-tag 2026-11-11 ~/Kits/oss-cad-suite
""",
    )
    parser.add_argument("path", nargs="?", type=Path, help="Suite root to install/update.")
    parser.add_argument("--check", action="store_true", help="Show available version without downloading.")
    parser.add_argument(
        "--force", action="store_true", help="Reinstall even if already current; replace non-suite directories."
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="Remove the previous installation after a successful update."
    )
    parser.add_argument("--no-resume", action="store_true", help="Discard any partial download and start over.")
    parser.add_argument("--no-validate", action="store_true", help="Skip the post-extract yosys -V runtime check.")
    parser.add_argument("--release-tag", help="Install a specific release tag instead of the latest.")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds (default: 60).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = UpdateOptions(
        path=args.path,
        check=args.check,
        force=args.force,
        keep_backup=not args.no_backup,
        resume=not args.no_resume,
        release_tag=args.release_tag,
        validate_runtime=not args.no_validate,
        timeout_s=args.timeout,
    )
    try:
        result = update_suite(options)
    except (UpdateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. A partial download may be retained for resume.", file=sys.stderr)
        return 130
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
