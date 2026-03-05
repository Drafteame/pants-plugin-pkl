"""Integration tests for the core PKL plugin registration (T04).

Verifies that the `pkl` backend registers target types correctly and that the
TargetFilesGenerator machinery generates per-file targets as expected.
"""

import pytest

from pants.engine.addresses import Address
from pants.engine.target import AllTargets
from pants.testutil.rule_runner import RuleRunner

from pkl import register
from pkl.target_types import (
    PklSourceField,
    PklSourceTarget,
    PklSourcesTarget,
    PklTestSourceField,
    PklTestTarget,
    PklTestsTarget,
    PklPackageTarget,
)


@pytest.fixture
def rule_runner() -> RuleRunner:
    """RuleRunner configured with the full set of rules and target types from register.py."""
    return RuleRunner(
        rules=register.rules(),
        target_types=register.target_types(),
    )


# ---------------------------------------------------------------------------
# Tests: pkl_sources → pkl_source generation
# ---------------------------------------------------------------------------


class TestPklSourcesGeneration:
    def test_generates_pkl_source_per_file(self, rule_runner: RuleRunner):
        """pkl_sources(name='src') generates a pkl_source for each matching .pkl file."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                "src/config.pkl": 'name = "myapp"\n',
            }
        )
        all_targets = rule_runner.request(AllTargets, [])
        source_targets = [
            t for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "src"
        ]
        assert len(source_targets) == 1
        assert source_targets[0][PklSourceField].value == "config.pkl"

    def test_generates_multiple_pkl_sources(self, rule_runner: RuleRunner):
        """Multiple .pkl files each get their own pkl_source target."""
        rule_runner.write_files(
            {
                "multi/BUILD": "pkl_sources(name='lib')\n",
                "multi/config.pkl": 'name = "myapp"\n',
                "multi/settings.pkl": "port = 8080\n",
            }
        )
        all_targets = rule_runner.request(AllTargets, [])
        source_targets = [
            t for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "multi"
        ]
        assert len(source_targets) == 2
        source_files = {t[PklSourceField].value for t in source_targets}
        assert source_files == {"config.pkl", "settings.pkl"}

    def test_get_target_for_pkl_sources_generator(self, rule_runner: RuleRunner):
        """Can resolve the pkl_sources generator target by address."""
        rule_runner.write_files(
            {
                "pkg/BUILD": "pkl_sources(name='src')\n",
                "pkg/config.pkl": 'name = "myapp"\n',
            }
        )
        tgt = rule_runner.get_target(Address("pkg", target_name="src"))
        assert tgt.alias == "pkl_sources"


# ---------------------------------------------------------------------------
# Tests: pkl_tests → pkl_test generation
# ---------------------------------------------------------------------------


class TestPklTestsGeneration:
    def test_generates_pkl_test_per_file(self, rule_runner: RuleRunner):
        """pkl_tests(name='tests') generates a pkl_test for each matching test file."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_tests(name='tests')\n",
                "tests/math_test.pkl": 'amends "pkl:test"\n',
            }
        )
        all_targets = rule_runner.request(AllTargets, [])
        test_targets = [
            t for t in all_targets
            if t.alias == "pkl_test" and t.address.spec_path == "tests"
        ]
        assert len(test_targets) == 1
        assert test_targets[0][PklTestSourceField].value == "math_test.pkl"

    def test_generates_multiple_pkl_tests(self, rule_runner: RuleRunner):
        """Multiple test .pkl files each get their own pkl_test target."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_tests(name='tests')\n",
                "tests/math_test.pkl": 'amends "pkl:test"\n',
                "tests/string_test.pkl": 'amends "pkl:test"\n',
            }
        )
        all_targets = rule_runner.request(AllTargets, [])
        test_targets = [
            t for t in all_targets
            if t.alias == "pkl_test" and t.address.spec_path == "tests"
        ]
        assert len(test_targets) == 2
        test_files = {t[PklTestSourceField].value for t in test_targets}
        assert test_files == {"math_test.pkl", "string_test.pkl"}

    def test_get_target_for_pkl_tests_generator(self, rule_runner: RuleRunner):
        """Can resolve the pkl_tests generator target by address."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_tests(name='tests')\n",
                "tests/math_test.pkl": 'amends "pkl:test"\n',
            }
        )
        tgt = rule_runner.get_target(Address("tests", target_name="tests"))
        assert tgt.alias == "pkl_tests"


# ---------------------------------------------------------------------------
# Tests: target_types() and rules() return values
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_target_types_includes_all_five(self):
        """register.target_types() exposes all five PKL target types."""
        types = register.target_types()
        aliases = {t.alias for t in types}
        assert aliases == {
            "pkl_source",
            "pkl_sources",
            "pkl_test",
            "pkl_tests",
            "pkl_package",
        }

    def test_rules_returns_a_list(self):
        """register.rules() must return a list (may be empty at this stage)."""
        rules = register.rules()
        assert isinstance(rules, list)
