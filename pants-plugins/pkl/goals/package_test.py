"""Tests for the PKL packaging goal (`pants package`).

Uses RuleRunner to exercise `package_pkl` with a real pkl binary.
Requires network access on first run to download pkl.
"""

from __future__ import annotations

import pytest

from pants.core.goals.package import BuiltPackage
from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.addresses import Address
from pants.engine.rules import QueryRule
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.goals.package import PklPackageFieldSet, rules as package_rules
from pkl.target_types import PklPackageTarget, PklSourceTarget


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *pkl_register.rules(),
            *source_files_rules(),
            *external_tool_rules(),
            *package_rules(),
            QueryRule(BuiltPackage, [PklPackageFieldSet]),
        ],
        target_types=pkl_register.target_types(),
    )


_CONFIG_PKL = 'name = "myapp"\nport = 8080\ndebug = false\n'


def _run_package(rule_runner: RuleRunner, address: Address) -> BuiltPackage:
    rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})
    tgt = rule_runner.get_target(address)
    field_set = PklPackageFieldSet.create(tgt)
    return rule_runner.request(BuiltPackage, [field_set])


# ---------------------------------------------------------------------------
# Default output path (stem + extension)
# ---------------------------------------------------------------------------


class TestDefaultOutputPath:
    def test_default_json_path(self, rule_runner: RuleRunner) -> None:
        """When no output_path is given, the output file is named <stem>.json."""
        rule_runner.write_files(
            {
                "src/config.pkl": _CONFIG_PKL,
                "src/BUILD": 'pkl_package(name="pkg", source="config.pkl", output_format="json")\n',
            }
        )
        result = _run_package(rule_runner, Address("src", target_name="pkg"))
        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "config.json"


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_output_artifact(self, rule_runner: RuleRunner) -> None:
        """Output format json produces a .json artifact."""
        rule_runner.write_files(
            {
                "src/config.pkl": _CONFIG_PKL,
                "src/BUILD": 'pkl_package(name="pkg", source="config.pkl", output_format="json")\n',
            }
        )
        result = _run_package(rule_runner, Address("src", target_name="pkg"))
        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "config.json"


# ---------------------------------------------------------------------------
# YAML output
# ---------------------------------------------------------------------------


class TestYamlOutput:
    def test_yaml_output_artifact(self, rule_runner: RuleRunner) -> None:
        """Output format yaml produces a .yaml artifact."""
        rule_runner.write_files(
            {
                "src/config.pkl": _CONFIG_PKL,
                "src/BUILD": 'pkl_package(name="pkg", source="config.pkl", output_format="yaml")\n',
            }
        )
        result = _run_package(rule_runner, Address("src", target_name="pkg"))
        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "config.yaml"


# ---------------------------------------------------------------------------
# Custom output path
# ---------------------------------------------------------------------------


class TestCustomOutputPath:
    def test_custom_path_used(self, rule_runner: RuleRunner) -> None:
        """When output_path is specified, the artifact has that path."""
        rule_runner.write_files(
            {
                "src/config.pkl": _CONFIG_PKL,
                "src/BUILD": (
                    'pkl_package(\n'
                    '  name="pkg",\n'
                    '  source="config.pkl",\n'
                    '  output_format="json",\n'
                    '  output_path="custom/config.json",\n'
                    ')\n'
                ),
            }
        )
        result = _run_package(rule_runner, Address("src", target_name="pkg"))
        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "custom/config.json"


# ---------------------------------------------------------------------------
# Expression mode
# ---------------------------------------------------------------------------


class TestExpressionMode:
    def test_expression_produces_artifact(self, rule_runner: RuleRunner) -> None:
        """Using expression='.name' evaluates a sub-expression of the module."""
        rule_runner.write_files(
            {
                "src/config.pkl": _CONFIG_PKL,
                "src/BUILD": (
                    'pkl_package(\n'
                    '  name="pkg",\n'
                    '  source="config.pkl",\n'
                    '  output_format="json",\n'
                    '  expression=".name",\n'
                    ')\n'
                ),
            }
        )
        result = _run_package(rule_runner, Address("src", target_name="pkg"))
        assert len(result.artifacts) == 1
        # The artifact path is still the stem-based default.
        assert result.artifacts[0].relpath == "config.json"
