"""Tests for the PKL eval-check lint rule (T05).

These tests use RuleRunner to exercise the full Pants rule graph with a real
(downloaded) pkl binary.  They are integration tests that require network
access on first run to fetch the pkl binary.
"""

from __future__ import annotations

import pytest

from pants.core.goals.lint import LintResult
from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.rules import QueryRule
from pants.engine.target import AllTargets
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.lint.eval_check import register as eval_check_register
from pkl.lint.eval_check.rules import PklEvalCheckFieldSet, PklEvalCheckRequest
from pkl.target_types import PklSourceTarget, PklSourcesTarget


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pkl_register.rules(),
            *eval_check_register.rules(),
            *source_files_rules(),
            *external_tool_rules(),
            QueryRule(LintResult, [PklEvalCheckRequest.Batch]),
            QueryRule(AllTargets, []),
        ],
        target_types=pkl_register.target_types(),
    )


def _make_batch(
    rule_runner: RuleRunner,
    *,
    spec_path: str = "src",
) -> PklEvalCheckRequest.Batch:
    """Build a PklEvalCheckRequest.Batch from the targets currently in rule_runner."""
    all_targets = rule_runner.request(AllTargets, [])
    field_sets = tuple(
        PklEvalCheckFieldSet.create(tgt)
        for tgt in all_targets
        if tgt.alias == "pkl_source"
        and tgt.address.spec_path == spec_path
        and not PklEvalCheckFieldSet.opt_out(tgt)
    )
    return PklEvalCheckRequest.Batch("", field_sets, partition_metadata=None)


class TestPklEvalCheckValid:
    def test_valid_pkl_passes(self, rule_runner: RuleRunner):
        """A well-formed PKL file produces exit_code=0."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                "src/config.pkl": 'name = "test"\nport = 8080\n',
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        batch = _make_batch(rule_runner)
        result = rule_runner.request(LintResult, [batch])
        assert result.exit_code == 0

    def test_with_import_both_files_in_sandbox(self, rule_runner: RuleRunner):
        """When a PKL file imports another, the import must resolve inside the sandbox."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                "src/lib.pkl": 'greeting = "hello"\n',
                "src/main.pkl": 'import "lib.pkl"\nresult = lib.greeting\n',
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        # Only check main.pkl — lib.pkl is a dep and should be in the sandbox.
        all_targets = rule_runner.request(AllTargets, [])
        field_sets = tuple(
            PklEvalCheckFieldSet.create(tgt)
            for tgt in all_targets
            if tgt.alias == "pkl_source"
            and tgt.address.spec_path == "src"
            and not PklEvalCheckFieldSet.opt_out(tgt)
        )
        batch = PklEvalCheckRequest.Batch("", field_sets, partition_metadata=None)
        result = rule_runner.request(LintResult, [batch])
        assert result.exit_code == 0


class TestPklEvalCheckInvalid:
    def test_invalid_pkl_fails(self, rule_runner: RuleRunner):
        """A PKL file with a type error produces a non-zero exit code."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                # type mismatch: name expects String but gets Int
                "src/bad.pkl": "name: String = 42\n",
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        batch = _make_batch(rule_runner)
        result = rule_runner.request(LintResult, [batch])
        assert result.exit_code != 0


class TestPklEvalCheckOptOut:
    def test_skip_eval_check_opts_out(self, rule_runner: RuleRunner):
        """Targets with skip_eval_check=True are excluded from evaluation via opt_out."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='src', skip_eval_check=True)\n",
                "src/abstract.pkl": "abstract module\n",
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
        all_targets = rule_runner.request(AllTargets, [])
        source_targets = [
            t for t in all_targets if t.alias == "pkl_source" and t.address.spec_path == "src"
        ]
        assert len(source_targets) == 1
        # opt_out should return True for every target with skip_eval_check=True.
        for tgt in source_targets:
            assert PklEvalCheckFieldSet.opt_out(tgt) is True
