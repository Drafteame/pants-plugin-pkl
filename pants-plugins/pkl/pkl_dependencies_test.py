"""Tests for PKL remote dependencies parsing and resolution."""

from __future__ import annotations

import json

import pytest

from pkl.pkl_dependencies import PklPackageEntry, _parse_deps_json


# ---------------------------------------------------------------------------
# _parse_deps_json — unit tests (pure Python, no Pants engine)
# ---------------------------------------------------------------------------


class TestParseDepsJson:
    def test_remote_deps_parsed(self):
        """Valid deps.json with remote deps is parsed correctly."""
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {
                "package://pkg.pkl-lang.org/pkl-pantry/pkl.toml@1": {
                    "type": "remote",
                    "uri": "projectpackage://pkg.pkl-lang.org/pkl-pantry/pkl.toml@1.0.2",
                    "checksums": {"sha256": "abc123"},
                },
                "package://example.com/mylib@0": {
                    "type": "remote",
                    "uri": "projectpackage://example.com/mylib@0.5.0",
                    "checksums": {"sha256": "def456"},
                },
            },
        }).encode()

        entries = _parse_deps_json(content)
        assert len(entries) == 2

        by_name = {e.name: e for e in entries}

        toml = by_name["pkl.toml"]
        assert toml.canonical_uri == "package://pkg.pkl-lang.org/pkl-pantry/pkl.toml@1"
        assert toml.resolved_uri == "projectpackage://pkg.pkl-lang.org/pkl-pantry/pkl.toml@1.0.2"
        assert toml.metadata_sha256 == "abc123"
        assert toml.host == "pkg.pkl-lang.org"
        assert toml.path_prefix == "pkl-pantry/pkl.toml"
        assert toml.version == "1.0.2"
        assert toml.metadata_url == "https://pkg.pkl-lang.org/pkl-pantry/pkl.toml@1.0.2"

        mylib = by_name["mylib"]
        assert mylib.host == "example.com"
        assert mylib.path_prefix == "mylib"
        assert mylib.version == "0.5.0"

    def test_local_deps_skipped(self):
        """Local dependencies are excluded from results."""
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {
                "package://example.com/remote@1": {
                    "type": "remote",
                    "uri": "projectpackage://example.com/remote@1.0.0",
                    "checksums": {"sha256": "aaa"},
                },
                "package://example.com/local@1": {
                    "type": "local",
                    "uri": "projectpackage://example.com/local@1.0.0",
                    "path": "../sibling",
                },
            },
        }).encode()

        entries = _parse_deps_json(content)
        assert len(entries) == 1
        assert entries[0].name == "remote"

    def test_empty_resolved_dependencies(self):
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {},
        }).encode()
        assert _parse_deps_json(content) == []

    def test_invalid_json(self):
        assert _parse_deps_json(b"not json {{{") == []

    def test_unknown_schema_version(self, caplog):
        """Unknown schemaVersion returns [] and logs a warning."""
        content = json.dumps({
            "schemaVersion": 99,
            "resolvedDependencies": {
                "package://example.com/foo@1": {
                    "type": "remote",
                    "uri": "projectpackage://example.com/foo@1.0.0",
                    "checksums": {"sha256": "abc"},
                },
            },
        }).encode()
        assert _parse_deps_json(content) == []
        assert "schemaVersion 99" in caplog.text
        assert "expected 1" in caplog.text

    def test_missing_sha256(self):
        """Entry without sha256 in checksums is skipped."""
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {
                "package://example.com/foo@1": {
                    "type": "remote",
                    "uri": "projectpackage://example.com/foo@1.0.0",
                    "checksums": {},
                },
            },
        }).encode()
        assert _parse_deps_json(content) == []

    def test_missing_checksums_key(self):
        """Entry without checksums key at all is skipped."""
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {
                "package://example.com/foo@1": {
                    "type": "remote",
                    "uri": "projectpackage://example.com/foo@1.0.0",
                },
            },
        }).encode()
        assert _parse_deps_json(content) == []

    def test_deep_path(self):
        """URIs with deeply nested paths are parsed correctly."""
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {
                "package://pkg.pkl-lang.org/pkl-pantry/org.openapis/openapi@0": {
                    "type": "remote",
                    "uri": "projectpackage://pkg.pkl-lang.org/pkl-pantry/org.openapis/openapi@0.2.1",
                    "checksums": {"sha256": "xyz"},
                },
            },
        }).encode()

        entries = _parse_deps_json(content)
        assert len(entries) == 1
        e = entries[0]
        assert e.host == "pkg.pkl-lang.org"
        assert e.path_prefix == "pkl-pantry/org.openapis/openapi"
        assert e.name == "openapi"
        assert e.version == "0.2.1"
        assert e.metadata_url == "https://pkg.pkl-lang.org/pkl-pantry/org.openapis/openapi@0.2.1"

    def test_empty_bytes(self):
        assert _parse_deps_json(b"") == []

    def test_missing_uri(self):
        """Entry without 'uri' field is skipped."""
        content = json.dumps({
            "schemaVersion": 1,
            "resolvedDependencies": {
                "package://example.com/foo@1": {
                    "type": "remote",
                    "checksums": {"sha256": "abc"},
                },
            },
        }).encode()
        assert _parse_deps_json(content) == []


# ---------------------------------------------------------------------------
# URI construction (for pkl download-package)
# ---------------------------------------------------------------------------


class TestUriConstruction:
    def test_checksum_embedded_uri(self):
        """Verify the URI format for pkl download-package."""
        entry = PklPackageEntry(
            canonical_uri="package://example.com/foo@1",
            resolved_uri="projectpackage://example.com/foo@1.2.3",
            metadata_sha256="abc123def456",
            host="example.com",
            path_prefix="foo",
            name="foo",
            version="1.2.3",
            metadata_url="https://example.com/foo@1.2.3",
        )
        pkg_uri = entry.resolved_uri.replace("projectpackage://", "package://")
        result = f"{pkg_uri}::sha256:{entry.metadata_sha256}"
        assert result == "package://example.com/foo@1.2.3::sha256:abc123def456"
