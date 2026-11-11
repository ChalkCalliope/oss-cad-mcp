from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from oss_cad_mcp.suite_update import (
    UpdateError,
    detect_platform,
    extract_archive,
    find_suite_root,
    install_archive,
    normalize_target,
    parse_asset_page,
    tag_to_version,
    version_is_newer,
)


def _make_suite_archive(tmp_path: Path, version: str, *, name: str = "suite.tgz") -> Path:
    source = tmp_path / f"src-{version}"
    suite = source / "oss-cad-suite"
    (suite / "bin").mkdir(parents=True)
    (suite / "share" / "yosys").mkdir(parents=True)
    yosys = suite / "bin" / "yosys"
    yosys.write_text("#!/bin/sh\necho Yosys fake\n", encoding="utf-8")
    yosys.chmod(0o755)
    (suite / "VERSION").write_text(version + "\n", encoding="utf-8")
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(suite, arcname="oss-cad-suite")
    return archive


def test_platform_detection() -> None:
    assert detect_platform("Linux", "x86_64").key == "linux-x64"
    assert detect_platform("Linux", "aarch64").key == "linux-arm64"
    assert detect_platform("Darwin", "arm64").key == "darwin-arm64"
    assert detect_platform("Darwin", "x86_64").key == "darwin-x64"
    with pytest.raises(UpdateError):
        detect_platform("Windows", "AMD64")


def test_versions() -> None:
    assert tag_to_version("2026-11-11") == "20261111"
    assert tag_to_version("20261111") == "20261111"
    assert version_is_newer("20261111", "20261105")
    assert not version_is_newer("20261105", "20261105")


def test_parse_asset_page() -> None:
    html = """
    <a href="/YosysHQ/oss-cad-suite-build/releases/download/2026-11-11/oss-cad-suite-linux-x64-20261111.tgz">
      <span aria-label="Copy to clipboard digest for oss-cad-suite-linux-x64-20261111.tgz"
            value="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"></span>
      <span>716 MB</span>
    </a>
    """
    asset = parse_asset_page(html, tag="2026-11-11", platform_key="linux-x64")
    assert asset is not None
    assert asset.name == "oss-cad-suite-linux-x64-20261111.tgz"
    assert asset.version == "20261111"
    assert asset.sha256 == "a" * 64
    assert asset.size_bytes == 716 * 1024**2
    assert asset.url.startswith("https://github.com/")


def test_extract_and_find_suite(tmp_path: Path) -> None:
    archive = _make_suite_archive(tmp_path, "20261111")
    staging = tmp_path / "staging"
    root = extract_archive(archive, staging)
    assert root.name == "oss-cad-suite"
    assert (root / "VERSION").read_text().strip() == "20261111"
    assert find_suite_root(staging) == root


def test_internal_symlink_is_preserved(tmp_path: Path) -> None:
    source = tmp_path / "link-src" / "oss-cad-suite"
    (source / "bin").mkdir(parents=True)
    (source / "libexec").mkdir()
    (source / "bin" / "yosys").write_text("#!/bin/sh\necho fake\n")
    (source / "bin" / "yosys").chmod(0o755)
    (source / "libexec" / "yosys-abc").write_text("fake abc")
    (source / "bin" / "yosys-abc").symlink_to("../libexec/yosys-abc")
    (source / "VERSION").write_text("20261111\n")
    archive = tmp_path / "links.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="oss-cad-suite")
    root = extract_archive(archive, tmp_path / "link-staging")
    assert (root / "bin" / "yosys-abc").is_symlink()
    assert (root / "bin" / "yosys-abc").read_text() == "fake abc"


def test_install_update_and_backup(tmp_path: Path) -> None:
    target = tmp_path / "installed" / "oss-cad-suite"
    first = install_archive(_make_suite_archive(tmp_path, "20261105"), target, validate_runtime=False)
    assert first.ok
    assert (target / "VERSION").read_text().strip() == "20261105"

    second = install_archive(
        _make_suite_archive(tmp_path, "20261111", name="suite2.tgz"),
        target,
        keep_backup=True,
        validate_runtime=False,
    )
    assert second.ok
    assert (target / "VERSION").read_text().strip() == "20261111"
    assert second.backup is not None
    assert (second.backup / "VERSION").read_text().strip() == "20261105"


def test_install_without_backup_and_force(tmp_path: Path) -> None:
    target = tmp_path / "installed" / "oss-cad-suite"
    install_archive(_make_suite_archive(tmp_path, "20261105"), target, validate_runtime=False)
    result = install_archive(
        _make_suite_archive(tmp_path, "20261111", name="suite2.tgz"),
        target,
        keep_backup=False,
        validate_runtime=False,
    )
    assert result.backup is None

    non_suite = tmp_path / "other"
    non_suite.mkdir()
    (non_suite / "keep.txt").write_text("not a suite")
    with pytest.raises(UpdateError):
        install_archive(_make_suite_archive(tmp_path, "20261112", name="suite3.tgz"), non_suite, validate_runtime=False)
    forced = install_archive(
        _make_suite_archive(tmp_path, "20261113", name="suite4.tgz"),
        non_suite,
        force=True,
        validate_runtime=False,
    )
    assert forced.ok
    assert (non_suite / "VERSION").read_text().strip() == "20261113"


def test_normalize_bin_path(tmp_path: Path) -> None:
    target = tmp_path / "oss-cad-suite"
    install_archive(_make_suite_archive(tmp_path, "20261105"), target, validate_runtime=False)
    assert normalize_target(target / "bin") == target.resolve()


def test_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../escape.txt")
        payload = b"nope"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(UpdateError):
        extract_archive(archive, tmp_path / "staging")


def test_rejects_escaping_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "evil-link.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("oss-cad-suite/bin/evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        tar.addfile(info)
    with pytest.raises(UpdateError):
        extract_archive(archive, tmp_path / "link-staging")
