"""Tests for PklTool subsystem."""

import pytest

from pants.engine.platform import Platform
from pants.testutil.option_util import create_subsystem

from pkl.subsystem import PklTool


def make_tool(version: str = PklTool.default_version) -> PklTool:
    """Instantiate PklTool with the options system properly initialised."""
    return create_subsystem(PklTool, version=version)


class TestPklToolUrls:
    """Verify that generate_url() produces correct download URLs for each platform."""

    @pytest.mark.parametrize(
        "pants_platform, expected_suffix",
        [
            ("macos_arm64", "macos-aarch64"),
            ("macos_x86_64", "macos-amd64"),
            ("linux_x86_64", "linux-amd64"),
            ("linux_arm64", "linux-aarch64"),
        ],
    )
    def test_generate_url_per_platform(self, pants_platform: str, expected_suffix: str):
        tool = make_tool()
        plat = Platform(pants_platform)
        url = tool.generate_url(plat)
        expected = (
            f"https://github.com/apple/pkl/releases/download/"
            f"{PklTool.default_version}/pkl-{expected_suffix}"
        )
        assert url == expected

    @pytest.mark.parametrize(
        "pants_platform, expected_suffix",
        [
            ("macos_arm64", "macos-aarch64"),
            ("macos_x86_64", "macos-amd64"),
            ("linux_x86_64", "linux-amd64"),
            ("linux_arm64", "linux-aarch64"),
        ],
    )
    def test_generate_exe_per_platform(self, pants_platform: str, expected_suffix: str):
        tool = make_tool()
        plat = Platform(pants_platform)
        exe = tool.generate_exe(plat)
        assert exe == f"./pkl-{expected_suffix}"

    def test_generate_url_uses_current_version(self):
        tool = make_tool()
        plat = Platform("linux_x86_64")
        url = tool.generate_url(plat)
        assert PklTool.default_version in url

    def test_generate_exe_uses_correct_prefix(self):
        tool = make_tool()
        for plat_str in ("macos_arm64", "macos_x86_64", "linux_x86_64", "linux_arm64"):
            plat = Platform(plat_str)
            exe = tool.generate_exe(plat)
            assert exe.startswith("./pkl-")


class TestPklToolKnownVersions:
    """Verify that default_known_versions is fully populated."""

    def test_known_versions_populated(self):
        assert len(PklTool.default_known_versions) == 4

    def test_known_versions_format(self):
        """Each entry must match 'version|platform|sha256|byte_length'."""
        for entry in PklTool.default_known_versions:
            parts = entry.split("|")
            assert len(parts) == 4, f"Bad format: {entry!r}"
            version, platform, sha256, byte_length = parts
            assert version == PklTool.default_version
            assert len(sha256) == 64, f"sha256 not 64 chars: {entry!r}"
            assert byte_length.isdigit(), f"byte_length not numeric: {entry!r}"

    def test_known_versions_cover_all_platforms(self):
        platforms_in_versions = {entry.split("|")[1] for entry in PklTool.default_known_versions}
        expected_platforms = {"macos_arm64", "macos_x86_64", "linux_x86_64", "linux_arm64"}
        assert platforms_in_versions == expected_platforms

    def test_known_versions_version_is_default(self):
        for entry in PklTool.default_known_versions:
            assert entry.startswith(PklTool.default_version + "|")
