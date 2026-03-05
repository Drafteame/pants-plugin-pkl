"""Tests for PKL tailor rules.

Tests use RuleRunner with the tailor rules to verify that BUILD targets are
suggested for .pkl files in various configurations.
"""

from __future__ import annotations

import pytest

from pants.core.goals.tailor import (
    AllOwnedSources,
    PutativeTargets,
    rules as tailor_rules,
)
from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.goals.tailor import PutativePklTargetsRequest, rules as pkl_tailor_rules



@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pkl_register.rules(),
            *tailor_rules(),
            *pkl_tailor_rules(),
            *external_tool_rules(),
            *source_files_rules(),
            QueryRule(PutativeTargets, [PutativePklTargetsRequest]),
        ],
        target_types=pkl_register.target_types(),
    )


def _make_request(dirs: tuple[str, ...] = ("",)) -> PutativePklTargetsRequest:
    return PutativePklTargetsRequest(dirs)


class TestTailorSourceFiles:
    def test_suggests_pkl_sources_for_source_files(self, rule_runner: RuleRunner) -> None:
        """Plain .pkl files with no existing targets get a pkl_sources() suggestion."""
        rule_runner.write_files(
            {
                "src/config.pkl": 'name = "test"\n',
                "src/utils.pkl": 'greeting = "hi"\n',
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("src",))])
        aliases = {pt.type_alias for pt in result}
        assert "pkl_sources" in aliases

    def test_sources_grouped_by_directory(self, rule_runner: RuleRunner) -> None:
        """A single pkl_sources target is suggested per directory."""
        rule_runner.write_files(
            {
                "src/config.pkl": 'name = "test"\n',
                "src/utils.pkl": 'greeting = "hi"\n',
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("src",))])
        src_pts = [pt for pt in result if pt.type_alias == "pkl_sources" and pt.path == "src"]
        assert len(src_pts) == 1


class TestTailorTestFiles:
    def test_suggests_pkl_tests_for_test_files(self, rule_runner: RuleRunner) -> None:
        """Files containing `amends "pkl:test"` get a pkl_tests() suggestion."""
        rule_runner.write_files(
            {
                "tests/math_test.pkl": 'amends "pkl:test"\nfacts { ["ok"] { true } }\n',
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("tests",))])
        aliases = {pt.type_alias for pt in result}
        assert "pkl_tests" in aliases

    def test_test_detection_by_content_not_name(self, rule_runner: RuleRunner) -> None:
        """Test detection uses content (`amends "pkl:test"`), not filename patterns."""
        rule_runner.write_files(
            {
                # Name doesn't match test patterns but content has the marker.
                "src/check.pkl": 'amends "pkl:test"\nfacts { ["pass"] { true } }\n',
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("src",))])
        aliases = {pt.type_alias for pt in result}
        assert "pkl_tests" in aliases
        assert "pkl_sources" not in aliases


class TestTailorMixed:
    def test_mixed_directory_generates_both_targets(self, rule_runner: RuleRunner) -> None:
        """A directory with both source and test files gets both pkl_sources and pkl_tests."""
        rule_runner.write_files(
            {
                "src/config.pkl": 'name = "app"\n',
                "src/config_test.pkl": 'amends "pkl:test"\nfacts { ["ok"] { true } }\n',
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("src",))])
        aliases = {pt.type_alias for pt in result}
        assert "pkl_sources" in aliases
        assert "pkl_tests" in aliases


class TestTailorAlreadyOwned:
    def test_already_owned_files_not_suggested(self, rule_runner: RuleRunner) -> None:
        """Files already in a BUILD target are not suggested again."""
        rule_runner.write_files(
            {
                "src/config.pkl": 'name = "test"\n',
                "src/BUILD": "pkl_sources(name='src')\n",
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("src",))])
        # config.pkl is already owned, so nothing should be suggested.
        assert len(result) == 0


class TestTailorPklProject:
    def test_pklproject_file_excluded(self, rule_runner: RuleRunner) -> None:
        """PklProject files are not included in any generated target."""
        rule_runner.write_files(
            {
                "src/PklProject": "amends \"pkl:Project\"\n",
                "src/config.pkl": 'name = "test"\n',
            }
        )
        result = rule_runner.request(PutativeTargets, [_make_request(("src",))])
        # PklProject should not appear in triggering_sources of any suggestion.
        for pt in result:
            assert "PklProject" not in pt.triggering_sources
