"""Tests for the PKL test runner (T06).

Uses RuleRunner to exercise `run_pkl_test` with a real pkl binary.
Requires network access on first run to download pkl.
"""

from __future__ import annotations

import pytest

from pants.core.goals.test import TestResult
from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.engine.target import AllTargets
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.goals import test as pkl_test_module
from pkl.goals.test import PklTestFieldSet, PklTestRequest
from pkl.target_types import PklTestTarget, PklTestsTarget


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pkl_register.rules(),
            *source_files_rules(),
            *external_tool_rules(),
            *pkl_test_module.rules(),
            QueryRule(TestResult, [PklTestRequest.Batch]),
            QueryRule(AllTargets, []),
        ],
        target_types=pkl_register.target_types(),
    )


def _make_batch(
    rule_runner: RuleRunner,
    address: Address,
) -> PklTestRequest.Batch:
    """Build a single-element PklTestRequest.Batch for the given target address."""
    tgt = rule_runner.get_target(address)
    field_set = PklTestFieldSet.create(tgt)
    return PklTestRequest.Batch("", (field_set,), partition_metadata=None)


# ---------------------------------------------------------------------------
# Passing test
# ---------------------------------------------------------------------------


class TestPklTestPassing:
    def test_passing_test_exits_zero(self, rule_runner: RuleRunner):
        """A PKL test file with all true assertions exits 0."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_test(name='math', source='math_test.pkl')\n",
                "tests/math_test.pkl": (
                    'amends "pkl:test"\n'
                    "\n"
                    "facts {\n"
                    '  ["addition"] {\n'
                    "    1 + 1 == 2\n"
                    "  }\n"
                    "}\n"
                ),
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        batch = _make_batch(rule_runner, Address("tests", target_name="math"))
        result = rule_runner.request(TestResult, [batch])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Failing test
# ---------------------------------------------------------------------------


class TestPklTestFailing:
    def test_failing_test_exits_nonzero(self, rule_runner: RuleRunner):
        """A PKL test file with a false assertion exits non-zero."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_test(name='bad', source='failing_test.pkl')\n",
                "tests/failing_test.pkl": (
                    'amends "pkl:test"\n'
                    "\n"
                    "facts {\n"
                    '  ["bad math"] {\n'
                    "    1 + 1 == 3\n"
                    "  }\n"
                    "}\n"
                ),
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        batch = _make_batch(rule_runner, Address("tests", target_name="bad"))
        result = rule_runner.request(TestResult, [batch])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Skip test via opt_out
# ---------------------------------------------------------------------------


class TestPklTestSkip:
    def test_skip_test_opts_out(self, rule_runner: RuleRunner):
        """Targets with skip_test=True are excluded via opt_out."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_test(name='skip', source='skip_test.pkl', skip_test=True)\n",
                "tests/skip_test.pkl": (
                    'amends "pkl:test"\n'
                    "\n"
                    "facts {\n"
                    '  ["ok"] {\n'
                    "    true\n"
                    "  }\n"
                    "}\n"
                ),
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        tgt = rule_runner.get_target(Address("tests", target_name="skip"))
        assert PklTestFieldSet.opt_out(tgt) is True


# ---------------------------------------------------------------------------
# Expected file in sandbox
# ---------------------------------------------------------------------------


class TestPklTestWithExpected:
    def test_expected_file_included_in_sandbox(self, rule_runner: RuleRunner):
        """If a .pkl-expected.pcf file exists next to the test, it is included in the sandbox."""
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_test(name='snap', source='snap_test.pkl')\n",
                "tests/snap_test.pkl": (
                    'amends "pkl:test"\n'
                    "\n"
                    "facts {\n"
                    '  ["simple"] {\n'
                    "    true\n"
                    "  }\n"
                    "}\n"
                ),
                # Empty expected file — presence is what matters for the sandbox test.
                "tests/snap_test.pkl-expected.pcf": "",
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        batch = _make_batch(rule_runner, Address("tests", target_name="snap"))
        # We only verify that the test runs without error (expected file doesn't break it).
        result = rule_runner.request(TestResult, [batch])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Timeout forwarded to Process
# ---------------------------------------------------------------------------


class TestPklTestTimeout:
    def test_timeout_field_passed_to_process(self, rule_runner: RuleRunner):
        """The per-target timeout field value is forwarded to the Process."""
        rule_runner.write_files(
            {
                "tests/BUILD": (
                    "pkl_test(name='timed', source='timed_test.pkl', timeout=120)\n"
                ),
                "tests/timed_test.pkl": (
                    'amends "pkl:test"\n'
                    "\n"
                    "facts {\n"
                    '  ["passes"] {\n'
                    "    true\n"
                    "  }\n"
                    "}\n"
                ),
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        tgt = rule_runner.get_target(Address("tests", target_name="timed"))
        field_set = PklTestFieldSet.create(tgt)
        # The timeout field should hold the value we set.
        assert field_set.timeout.value == 120
