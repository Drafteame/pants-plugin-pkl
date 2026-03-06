"""Tests for the PKL formatter rule.

Uses RuleRunner with the real pkl binary to verify `pkl format --write` behaviour.
Requires network access on first run to download pkl.
"""

from __future__ import annotations

import pytest

from pants.core.goals.fmt import FmtResult
from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.fs import PathGlobs, Snapshot
from pants.engine.rules import QueryRule
from pants.engine.target import AllTargets
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.lint.fmt import register as fmt_register
from pkl.lint.fmt.rules import PklFmtFieldSet, PklFmtRequest, _PKL_FORMAT_MIN_VERSION
from pkl.subsystem import _version_gte



@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pkl_register.rules(),
            *fmt_register.rules(),
            *source_files_rules(),
            *external_tool_rules(),
            QueryRule(FmtResult, [PklFmtRequest.Batch]),
            QueryRule(AllTargets, []),
            QueryRule(Snapshot, [PathGlobs]),
        ],
        target_types=pkl_register.target_types(),
    )


def _run_fmt(
    rule_runner: RuleRunner,
    files: dict[str, str],
    *,
    spec_path: str = "src",
) -> FmtResult:
    """Write files, build a batch, and request FmtResult."""
    rule_runner.write_files(files)
    rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

    all_targets = rule_runner.request(AllTargets, [])
    field_sets = tuple(
        PklFmtFieldSet.create(tgt)
        for tgt in all_targets
        if tgt.alias == "pkl_source" and tgt.address.spec_path == spec_path
    )

    snapshot = rule_runner.request(
        Snapshot,
        [PathGlobs([f"{spec_path}/*.pkl"])],
    )

    batch = PklFmtRequest.Batch("", field_sets, partition_metadata=None, snapshot=snapshot)
    return rule_runner.request(FmtResult, [batch])


class TestPklFmtAlreadyFormatted:
    def test_already_formatted_no_change(self, rule_runner: RuleRunner):
        """A properly formatted PKL file produces did_change=False."""
        result = _run_fmt(
            rule_runner,
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                # PKL canonical style: spaces around `=`, one property per line.
                "src/config.pkl": 'name = "hello"\nage = 30\n',
            },
        )
        assert result.did_change is False


class TestPklFmtNeedsFormatting:
    def test_needs_formatting_produces_change(self, rule_runner: RuleRunner):
        """A PKL file with formatting violations produces did_change=True."""
        result = _run_fmt(
            rule_runner,
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                # Missing spaces around `=` — pkl format will normalise this.
                "src/messy.pkl": 'name="hello"\nage=30\n',
            },
        )
        assert result.did_change is True


class TestPklFmtMultipleFiles:
    def test_multiple_files_formatted_together(self, rule_runner: RuleRunner):
        """Multiple PKL files in a single batch are all formatted without error."""
        result = _run_fmt(
            rule_runner,
            {
                "src/BUILD": "pkl_sources(name='src')\n",
                "src/a.pkl": 'x = "a"\n',
                "src/b.pkl": 'y = "b"\n',
            },
        )
        # Both files are already properly formatted.
        assert result.did_change is False


class TestPklFmtVersionGate:
    """Unit tests for the _PKL_FORMAT_MIN_VERSION constant and its guard logic.

    These tests do not spin up a RuleRunner; they verify the pure-Python version
    comparison that protects pkl_fmt from running against a binary too old to have
    the `format` subcommand.
    """

    def test_min_version_constant_is_0_30_0(self):
        assert _PKL_FORMAT_MIN_VERSION == "0.30.0"

    @pytest.mark.parametrize(
        "version",
        ["0.27.0", "0.28.0", "0.29.0", "0.29.9"],
    )
    def test_versions_below_threshold_fail_guard(self, version: str):
        assert not _version_gte(version, _PKL_FORMAT_MIN_VERSION), (
            f"{version!r} should be below {_PKL_FORMAT_MIN_VERSION!r}"
        )

    @pytest.mark.parametrize(
        "version",
        ["0.30.0", "0.31.0", "0.32.0", "1.0.0"],
    )
    def test_versions_at_or_above_threshold_pass_guard(self, version: str):
        assert _version_gte(version, _PKL_FORMAT_MIN_VERSION), (
            f"{version!r} should be >= {_PKL_FORMAT_MIN_VERSION!r}"
        )
