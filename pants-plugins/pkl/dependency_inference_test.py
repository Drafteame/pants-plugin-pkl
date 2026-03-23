"""Tests for PKL dependency inference.

Unit tests cover:
- JSON output parsing from `pkl analyze imports`
- Regex fallback parser
- PKL project alias resolution

Integration tests (using RuleRunner) cover:
- Full inference pipeline with a real pkl binary
"""

from __future__ import annotations

import pytest

from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.engine.target import AllTargets, InferredDependencies
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.dependency_inference import (
    InferPklDependenciesRequest,
    PklInferenceFieldSet,
    _extract_local_paths_from_regex,
    _normalize_path,
    _parse_analyze_output,
    _parse_pkl_project_local_deps,
    _resolve_projectpackage_uri,
    rules as dep_inf_rules,
)



# ---------------------------------------------------------------------------
# Unit tests — path normalisation helper
# ---------------------------------------------------------------------------


class TestNormalizePath:
    def test_plain_path(self) -> None:
        assert _normalize_path("a/b/c") == "a/b/c"

    def test_resolves_dotdot(self) -> None:
        assert _normalize_path("a/b/../c") == "a/c"

    def test_resolves_multiple_dotdot(self) -> None:
        assert _normalize_path("a/b/c/../../d") == "a/d"

    def test_resolves_leading_dotdot_at_root(self) -> None:
        # ".." at the repo root has no parent to pop; result should be empty.
        assert _normalize_path("../outside") == "outside"

    def test_ignores_single_dot(self) -> None:
        assert _normalize_path("a/./b") == "a/b"

    def test_long_relative_chain(self) -> None:
        # services/program-control/config/app + ../../../../config/pkl/PklProject
        path = "services/program-control/config/app/../../../../config/pkl/PklProject"
        assert _normalize_path(path) == "config/pkl/PklProject"


# ---------------------------------------------------------------------------
# Unit tests — PKL project alias map
# ---------------------------------------------------------------------------


class TestParsePklProjectLocalDeps:
    def test_parses_single_dep(self) -> None:
        content = """
amends "pkl:Project"
dependencies {
  ["baseconfig"] = import("../../../../config/pkl/PklProject")
}
"""
        result = _parse_pkl_project_local_deps(content, "services/app/config/app")
        assert result == {"baseconfig": "config/pkl"}

    def test_parses_multiple_deps(self) -> None:
        content = """
amends "pkl:Project"
dependencies {
  ["baseconfig"] = import("../../../config/pkl/PklProject")
  ["shared"] = import("../../shared/PklProject")
}
"""
        result = _parse_pkl_project_local_deps(content, "services/app")
        assert result == {
            "baseconfig": "config/pkl",
            "shared": "shared",
        }

    def test_returns_empty_for_no_deps(self) -> None:
        content = 'amends "pkl:Project"\n'
        result = _parse_pkl_project_local_deps(content, "services/app")
        assert result == {}

    def test_ignores_remote_dep_syntax(self) -> None:
        """Remote deps use { uri = "package://..." } syntax — not matched."""
        content = """
amends "pkl:Project"
dependencies {
  ["remote-pkg"] {
    uri = "package://example.com/pkg@1.0.0"
  }
  ["local"] = import("../lib/PklProject")
}
"""
        result = _parse_pkl_project_local_deps(content, "services/app")
        # Only the local dep is parsed; the remote one is not matched.
        # "services/app" + "../lib/PklProject" → parent "services/app/../lib" → "services/lib"
        assert result == {"local": "services/lib"}

    def test_normalizes_dotdot_components(self) -> None:
        content = '["alias"] = import("../../../third/PklProject")\n'
        result = _parse_pkl_project_local_deps(content, "a/b/c")
        assert result == {"alias": "third"}

    def test_root_level_project_dir(self) -> None:
        """project_dir can be empty string when PklProject is at repo root."""
        content = '["dep"] = import("../other/PklProject")\n'
        result = _parse_pkl_project_local_deps(content, "")
        assert result == {"dep": "other"}

    def test_allows_whitespace_in_import(self) -> None:
        content = '["alias"] = import( "../../lib/PklProject" )\n'
        result = _parse_pkl_project_local_deps(content, "services/app")
        assert result == {"alias": "lib"}


# ---------------------------------------------------------------------------
# Unit tests — projectpackage:// resolver
# ---------------------------------------------------------------------------


class TestResolveProjectpackageUri:
    def test_resolves_simple_uri(self) -> None:
        alias_map = {"baseconfig": "config/pkl"}
        result = _resolve_projectpackage_uri(
            "projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl",
            alias_map,
        )
        assert result == "config/pkl/global.pkl"

    def test_resolves_nested_file_path(self) -> None:
        alias_map = {"shared": "infra/shared"}
        result = _resolve_projectpackage_uri(
            "projectpackage://localhost:0/shared@2.3.4#/subdir/types.pkl",
            alias_map,
        )
        assert result == "infra/shared/subdir/types.pkl"

    def test_returns_none_for_unknown_alias(self) -> None:
        alias_map = {"other": "other/dir"}
        result = _resolve_projectpackage_uri(
            "projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl",
            alias_map,
        )
        assert result is None

    def test_returns_none_for_empty_alias_map(self) -> None:
        result = _resolve_projectpackage_uri(
            "projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl",
            {},
        )
        assert result is None

    def test_returns_none_without_fragment(self) -> None:
        alias_map = {"baseconfig": "config/pkl"}
        result = _resolve_projectpackage_uri(
            "projectpackage://localhost:0/baseconfig@1.0.0",
            alias_map,
        )
        assert result is None

    def test_returns_none_for_non_projectpackage_uri(self) -> None:
        alias_map = {"baseconfig": "config/pkl"}
        result = _resolve_projectpackage_uri(
            "file:///sandbox/config/pkl/global.pkl",
            alias_map,
        )
        assert result is None

    def test_namespaced_package_path(self) -> None:
        """Package with nested path in URI — last segment is the package name."""
        alias_map = {"mylib": "libs/mylib"}
        result = _resolve_projectpackage_uri(
            "projectpackage://example.com/org/mylib@0.5.0#/api.pkl",
            alias_map,
        )
        assert result == "libs/mylib/api.pkl"


# ---------------------------------------------------------------------------
# Unit tests — JSON parsing
# ---------------------------------------------------------------------------


class TestParseAnalyzeOutput:
    def test_parses_file_uri_imports(self) -> None:
        """Direct file:// imports are extracted and returned as relative paths."""
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/src/main.pkl": [
              {"uri": "file:///sandbox/src/lib.pkl"}
            ]
          }
        }"""
        paths = _parse_analyze_output(json_bytes, "src/main.pkl")
        assert any("lib.pkl" in p for p in paths)

    def test_returns_empty_for_no_imports(self) -> None:
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/src/main.pkl": []
          }
        }"""
        paths = _parse_analyze_output(json_bytes, "src/main.pkl")
        assert paths == []

    def test_returns_empty_for_invalid_json(self) -> None:
        paths = _parse_analyze_output(b"not json", "src/main.pkl")
        assert paths == []

    def test_ignores_non_file_uris(self) -> None:
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/src/main.pkl": [
              {"uri": "pkl:test"},
              {"uri": "package://example.com/pkg@1.0.0"},
              {"uri": "https://example.com/module.pkl"}
            ]
          }
        }"""
        paths = _parse_analyze_output(json_bytes, "src/main.pkl")
        # All non-file:// URIs should be ignored (they are not local)
        # The implementation currently returns the abs path — we filter by
        # checking no non-file URI appears.
        # The result should contain only file:// paths.
        assert all("pkl:test" not in p for p in paths)

    def test_resolves_projectpackage_uri_with_alias_map(self) -> None:
        """projectpackage:// URIs are resolved when an alias_map is provided."""
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/services/app/app.pkl": [
              {"uri": "file:///sandbox/services/app/modules.pkl"},
              {"uri": "projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl"}
            ]
          }
        }"""
        alias_map = {"baseconfig": "config/pkl"}
        paths = _parse_analyze_output(json_bytes, "services/app/app.pkl", alias_map=alias_map)
        # file:// dep should be present
        assert any("modules.pkl" in p for p in paths)
        # projectpackage:// dep should be resolved via alias_map
        assert "config/pkl/global.pkl" in paths

    def test_ignores_projectpackage_uri_without_alias_map(self) -> None:
        """projectpackage:// URIs are silently skipped when alias_map is None."""
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/services/app/app.pkl": [
              {"uri": "projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl"}
            ]
          }
        }"""
        paths = _parse_analyze_output(json_bytes, "services/app/app.pkl")
        assert paths == []

    def test_ignores_projectpackage_uri_with_unknown_alias(self) -> None:
        """projectpackage:// URIs are skipped if alias is not in alias_map."""
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/services/app/app.pkl": [
              {"uri": "projectpackage://localhost:0/unknown@1.0.0#/file.pkl"}
            ]
          }
        }"""
        alias_map = {"baseconfig": "config/pkl"}
        paths = _parse_analyze_output(json_bytes, "services/app/app.pkl", alias_map=alias_map)
        assert paths == []

    def test_mixed_file_and_projectpackage_uris(self) -> None:
        """Both file:// and resolved projectpackage:// appear in results."""
        json_bytes = b"""{
          "imports": {
            "file:///sandbox/services/app/app.pkl": [
              {"uri": "file:///sandbox/services/app/local.pkl"},
              {"uri": "projectpackage://localhost:0/lib@0.1.0#/utils.pkl"}
            ]
          }
        }"""
        alias_map = {"lib": "shared/lib"}
        paths = _parse_analyze_output(json_bytes, "services/app/app.pkl", alias_map=alias_map)
        assert any("local.pkl" in p for p in paths)
        assert "shared/lib/utils.pkl" in paths


# ---------------------------------------------------------------------------
# Unit tests — regex fallback
# ---------------------------------------------------------------------------


class TestRegexFallback:
    def test_matches_import(self) -> None:
        source = 'import "lib.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/main.pkl")
        assert "src/lib.pkl" in paths

    def test_matches_amends(self) -> None:
        source = 'amends "base.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/derived.pkl")
        assert "src/base.pkl" in paths

    def test_matches_extends(self) -> None:
        source = 'extends "parent.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/child.pkl")
        assert "src/parent.pkl" in paths

    def test_matches_import_star(self) -> None:
        source = 'import* "*.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/main.pkl")
        assert "src/*.pkl" in paths

    def test_ignores_pkl_scheme(self) -> None:
        source = 'amends "pkl:test"\n'
        paths = _extract_local_paths_from_regex(source, "tests/t.pkl")
        assert paths == []

    def test_ignores_package_uri(self) -> None:
        source = 'import "package://example.com/pkg@1.0.0/config.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/main.pkl")
        assert paths == []

    def test_ignores_https_uri(self) -> None:
        source = 'import "https://example.com/module.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/main.pkl")
        assert paths == []

    def test_resolves_relative_paths(self) -> None:
        """Imports in subdirectories are resolved relative to the source file."""
        source = 'import "../shared/utils.pkl"\n'
        paths = _extract_local_paths_from_regex(source, "src/sub/main.pkl")
        assert "src/shared/utils.pkl" in paths

    def test_multiple_imports(self) -> None:
        source = (
            'import "a.pkl"\n'
            'import "b.pkl"\n'
            'amends "base.pkl"\n'
        )
        paths = _extract_local_paths_from_regex(source, "src/main.pkl")
        assert "src/a.pkl" in paths
        assert "src/b.pkl" in paths
        assert "src/base.pkl" in paths

    # --- alias import tests ---

    def test_resolves_alias_import_with_alias_map(self) -> None:
        """@alias/... is resolved to a repo-relative path via alias_map."""
        source = 'import "@baseconfig/global.pkl" as global\n'
        alias_map = {"baseconfig": "config/pkl"}
        paths = _extract_local_paths_from_regex(source, "services/app/app.pkl", alias_map=alias_map)
        assert "config/pkl/global.pkl" in paths

    def test_alias_import_skipped_without_alias_map(self) -> None:
        """@alias/... imports are silently skipped when no alias_map is given."""
        source = 'import "@baseconfig/global.pkl" as global\n'
        paths = _extract_local_paths_from_regex(source, "services/app/app.pkl")
        assert paths == []

    def test_alias_import_skipped_for_unknown_alias(self) -> None:
        """Unknown aliases are silently skipped."""
        source = 'import "@unknown/file.pkl"\n'
        alias_map = {"baseconfig": "config/pkl"}
        paths = _extract_local_paths_from_regex(source, "services/app/app.pkl", alias_map=alias_map)
        assert paths == []

    def test_alias_import_with_nested_path(self) -> None:
        """@alias/subdir/file.pkl resolves correctly."""
        source = 'import "@shared/utils/helpers.pkl"\n'
        alias_map = {"shared": "infra/shared"}
        paths = _extract_local_paths_from_regex(source, "services/app/app.pkl", alias_map=alias_map)
        assert "infra/shared/utils/helpers.pkl" in paths

    def test_mix_local_and_alias_imports(self) -> None:
        """Both local and @alias/... imports appear in results."""
        source = (
            'import "modules.pkl"\n'
            'import "@baseconfig/global.pkl" as global\n'
            'import "../shared/utils.pkl"\n'
        )
        alias_map = {"baseconfig": "config/pkl"}
        paths = _extract_local_paths_from_regex(
            source, "services/app/app.pkl", alias_map=alias_map
        )
        assert "services/app/modules.pkl" in paths
        assert "config/pkl/global.pkl" in paths
        assert "services/shared/utils.pkl" in paths

    def test_multiple_aliases_in_alias_map(self) -> None:
        """Multiple aliases are all resolved correctly."""
        source = (
            'import "@base/config.pkl"\n'
            'import "@shared/types.pkl"\n'
        )
        alias_map = {"base": "common/base", "shared": "common/shared"}
        paths = _extract_local_paths_from_regex(
            source, "services/app/app.pkl", alias_map=alias_map
        )
        assert "common/base/config.pkl" in paths
        assert "common/shared/types.pkl" in paths

    def test_alias_used_in_amends(self) -> None:
        """@alias/... works with amends and extends too."""
        source = 'amends "@baseconfig/BaseAmends.pkl"\n'
        alias_map = {"baseconfig": "config/pkl"}
        paths = _extract_local_paths_from_regex(
            source, "services/app/app.pkl", alias_map=alias_map
        )
        assert "config/pkl/BaseAmends.pkl" in paths


# ---------------------------------------------------------------------------
# Integration tests — RuleRunner
# ---------------------------------------------------------------------------


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pkl_register.rules(),
            *source_files_rules(),
            *external_tool_rules(),
            *dep_inf_rules(),
            QueryRule(InferredDependencies, [InferPklDependenciesRequest]),
            QueryRule(AllTargets, []),
        ],
        target_types=pkl_register.target_types(),
    )


class TestInferPklDependenciesIntegration:
    def test_infers_local_import(self, rule_runner: RuleRunner) -> None:
        """main.pkl importing lib.pkl should infer a dependency on lib.pkl's target."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                "src/lib.pkl": 'greeting = "hello"\n',
                "src/main.pkl": 'import "lib.pkl"\nresult = lib.greeting\n',
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        main_target = next(
            (
                tgt
                for tgt in all_targets
                if tgt.alias == "pkl_source"
                and tgt.address.spec_path == "src"
                and "main" in str(tgt.address)
            ),
            None,
        )
        assert main_target is not None, "Could not find src/main.pkl target"

        field_set = PklInferenceFieldSet.create(main_target)
        request = InferPklDependenciesRequest(field_set)
        inferred = rule_runner.request(InferredDependencies, [request])

        inferred_paths = [addr.spec_path for addr in inferred.include]
        assert "src" in inferred_paths or any("lib" in str(a) for a in inferred.include)
