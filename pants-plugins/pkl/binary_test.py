"""Tests for PklBinary resolution, version parsing, and version comparison."""

from __future__ import annotations

import pytest

from pkl.subsystem import (
    PklBinary,
    _expand_search_path,
    _parse_pkl_version,
    _version_gte,
    _version_tuple,
)
from pants.engine.env_vars import EnvironmentVars
from pants.engine.fs import EMPTY_DIGEST


# ---------------------------------------------------------------------------
# _parse_pkl_version
# ---------------------------------------------------------------------------


class TestParsePklVersion:
    def test_standard_linux(self):
        assert _parse_pkl_version("Pkl 0.28.0 (Linux, Native)") == "0.28.0"

    def test_standard_macos(self):
        assert _parse_pkl_version("Pkl 0.31.0 (macOS, Native)") == "0.31.0"

    def test_macos_aarch64(self):
        assert _parse_pkl_version("Pkl 0.30.0 (macOS (aarch64), Native)") == "0.30.0"

    def test_initial_release(self):
        assert _parse_pkl_version("Pkl 0.25.0 (Linux, Native)") == "0.25.0"

    def test_future_major(self):
        assert _parse_pkl_version("Pkl 1.0.0 (Linux, Native)") == "1.0.0"

    def test_prerelease_stripped(self):
        # The regex captures only the numeric portion.
        assert _parse_pkl_version("Pkl 0.32.0-dev (Linux, Native)") == "0.32.0"

    def test_empty_string(self):
        assert _parse_pkl_version("") is None

    def test_unrelated_output(self):
        assert _parse_pkl_version("not pkl output") is None

    def test_partial_pkl(self):
        assert _parse_pkl_version("Pkl") is None

    def test_pkl_no_version(self):
        assert _parse_pkl_version("Pkl abc") is None


# ---------------------------------------------------------------------------
# _version_tuple
# ---------------------------------------------------------------------------


class TestVersionTuple:
    def test_basic(self):
        assert _version_tuple("0.28.0") == (0, 28, 0)

    def test_large_numbers(self):
        assert _version_tuple("10.20.30") == (10, 20, 30)

    def test_zeros(self):
        assert _version_tuple("0.0.0") == (0, 0, 0)

    def test_single_digit(self):
        assert _version_tuple("1.2.3") == (1, 2, 3)


# ---------------------------------------------------------------------------
# _version_gte
# ---------------------------------------------------------------------------


class TestVersionGte:
    def test_greater(self):
        assert _version_gte("0.28.0", "0.27.0") is True

    def test_equal(self):
        assert _version_gte("0.27.0", "0.27.0") is True

    def test_less(self):
        assert _version_gte("0.26.3", "0.27.0") is False

    def test_patch_greater(self):
        assert _version_gte("0.27.1", "0.27.0") is True

    def test_patch_less(self):
        assert _version_gte("0.27.0", "0.27.1") is False

    def test_major_boundary(self):
        assert _version_gte("1.0.0", "0.99.0") is True

    def test_major_less(self):
        assert _version_gte("0.99.0", "1.0.0") is False

    def test_format_minimum(self):
        """0.30.0 is the minimum for pkl format."""
        assert _version_gte("0.30.0", "0.30.0") is True
        assert _version_gte("0.29.0", "0.30.0") is False
        assert _version_gte("0.31.0", "0.30.0") is True


# ---------------------------------------------------------------------------
# PklBinary dataclass
# ---------------------------------------------------------------------------


class TestPklBinary:
    def test_system_binary(self):
        b = PklBinary(
            exe="/usr/local/bin/pkl",
            digest=EMPTY_DIGEST,
            version="0.28.0",
            is_system=True,
        )
        assert b.exe == "/usr/local/bin/pkl"
        assert b.digest == EMPTY_DIGEST
        assert b.version == "0.28.0"
        assert b.is_system is True

    def test_downloaded_binary(self):
        b = PklBinary(
            exe="./pkl-macos-aarch64",
            digest=EMPTY_DIGEST,  # placeholder
            version="0.31.0",
            is_system=False,
        )
        assert b.is_system is False
        assert "pkl-macos" in b.exe

    def test_frozen(self):
        b = PklBinary(exe="pkl", digest=EMPTY_DIGEST, version="0.31.0", is_system=True)
        with pytest.raises(AttributeError):
            b.exe = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _expand_search_path
# ---------------------------------------------------------------------------


class TestExpandSearchPath:
    def test_expands_path_sentinel(self):
        env = EnvironmentVars({"PATH": "/usr/bin:/usr/local/bin"})
        result = _expand_search_path(["<PATH>"], env)
        assert result == ("/usr/bin", "/usr/local/bin")

    def test_preserves_literal_paths(self):
        env = EnvironmentVars({"PATH": "/usr/bin"})
        result = _expand_search_path(["/opt/custom/bin", "<PATH>"], env)
        assert result == ("/opt/custom/bin", "/usr/bin")

    def test_deduplicates_preserving_order(self):
        env = EnvironmentVars({"PATH": "/usr/bin:/opt/custom/bin:/usr/bin"})
        result = _expand_search_path(["/opt/custom/bin", "<PATH>"], env)
        assert result == ("/opt/custom/bin", "/usr/bin")

    def test_empty_path_env(self):
        env = EnvironmentVars({"PATH": ""})
        result = _expand_search_path(["<PATH>"], env)
        assert result == ()

    def test_missing_path_env(self):
        env = EnvironmentVars({})
        result = _expand_search_path(["<PATH>"], env)
        assert result == ()

    def test_no_sentinels(self):
        env = EnvironmentVars({"PATH": "/usr/bin"})
        result = _expand_search_path(["/a", "/b"], env)
        assert result == ("/a", "/b")

    def test_mixed_sentinels_and_literals(self):
        env = EnvironmentVars({"PATH": "/usr/bin:/usr/local/bin"})
        result = _expand_search_path(["/opt/pkl", "<PATH>", "/extra"], env)
        assert result == ("/opt/pkl", "/usr/bin", "/usr/local/bin", "/extra")
