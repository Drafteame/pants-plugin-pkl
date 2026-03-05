"""Tests for PKL target types and fields."""

import pytest

from pants.engine.addresses import Address
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    AllTargets,
    BoolField,
    IntField,
    MultipleSourcesField,
    SingleSourceField,
    StringField,
    StringSequenceField,
)
from pants.testutil.rule_runner import RuleRunner

from pkl.target_types import (
    PklExpressionField,
    PklExtraArgsField,
    PklGeneratingSourcesField,
    PklJunitReportsField,
    PklModulePathField,
    PklMultipleOutputField,
    PklMultipleOutputPathField,
    PklOutputFormatField,
    PklOutputPathField,
    PklPackageTarget,
    PklProjectDirField,
    PklSkipEvalCheckField,
    PklSkipTestField,
    PklSourceField,
    PklSourceTarget,
    PklSourcesTarget,
    PklTestGeneratingSourcesField,
    PklTestSourceField,
    PklTestTarget,
    PklTestTimeoutField,
    PklTestsTarget,
)


# ---------------------------------------------------------------------------
# Field unit tests
# ---------------------------------------------------------------------------


class TestSourceFields:
    def test_pkl_source_field_is_single_source(self):
        assert issubclass(PklSourceField, SingleSourceField)

    def test_pkl_source_field_extensions(self):
        assert PklSourceField.expected_file_extensions == (".pkl",)

    def test_pkl_generating_sources_field_is_multiple_sources(self):
        assert issubclass(PklGeneratingSourcesField, MultipleSourcesField)

    def test_pkl_generating_sources_field_extensions(self):
        assert PklGeneratingSourcesField.expected_file_extensions == (".pkl",)

    def test_pkl_generating_sources_field_default(self):
        assert PklGeneratingSourcesField.default == (
            "*.pkl",
            "!*_test.pkl",
            "!*Test.pkl",
            "!test_*.pkl",
            "!PklProject",
        )

    def test_pkl_test_source_field_is_single_source(self):
        assert issubclass(PklTestSourceField, SingleSourceField)

    def test_pkl_test_source_field_extensions(self):
        assert PklTestSourceField.expected_file_extensions == (".pkl",)

    def test_pkl_test_generating_sources_field_default(self):
        assert PklTestGeneratingSourcesField.default == (
            "*_test.pkl",
            "*Test.pkl",
            "test_*.pkl",
        )


class TestConfigFields:
    def test_output_format_alias(self):
        assert PklOutputFormatField.alias == "output_format"

    def test_output_format_default(self):
        assert PklOutputFormatField.default == "json"

    def test_output_format_valid_choices(self):
        assert PklOutputFormatField.valid_choices == (
            "json",
            "yaml",
            "plist",
            "properties",
            "pcf",
            "textproto",
            "xml",
            "jsonnet",
        )

    def test_output_path_alias(self):
        assert PklOutputPathField.alias == "output_path"

    def test_output_path_default(self):
        assert PklOutputPathField.default is None

    def test_multiple_outputs_alias(self):
        assert PklMultipleOutputField.alias == "multiple_outputs"

    def test_multiple_outputs_default(self):
        assert PklMultipleOutputField.default is False

    def test_multiple_output_path_alias(self):
        assert PklMultipleOutputPathField.alias == "multiple_output_path"

    def test_multiple_output_path_default(self):
        assert PklMultipleOutputPathField.default == "."

    def test_expression_alias(self):
        assert PklExpressionField.alias == "expression"

    def test_expression_default(self):
        assert PklExpressionField.default is None

    def test_project_dir_alias(self):
        assert PklProjectDirField.alias == "project_dir"

    def test_project_dir_default(self):
        assert PklProjectDirField.default is None

    def test_module_path_alias(self):
        assert PklModulePathField.alias == "module_path"

    def test_module_path_default(self):
        assert PklModulePathField.default is None

    def test_extra_args_alias(self):
        assert PklExtraArgsField.alias == "extra_args"

    def test_extra_args_default(self):
        assert PklExtraArgsField.default == ()

    def test_skip_test_alias(self):
        assert PklSkipTestField.alias == "skip_test"

    def test_skip_test_default(self):
        assert PklSkipTestField.default is False

    def test_test_timeout_alias(self):
        assert PklTestTimeoutField.alias == "timeout"

    def test_test_timeout_default(self):
        assert PklTestTimeoutField.default is None

    def test_junit_reports_alias(self):
        assert PklJunitReportsField.alias == "junit_reports"

    def test_junit_reports_default(self):
        assert PklJunitReportsField.default is False

    def test_skip_eval_check_alias(self):
        assert PklSkipEvalCheckField.alias == "skip_eval_check"

    def test_skip_eval_check_default(self):
        assert PklSkipEvalCheckField.default is False


# ---------------------------------------------------------------------------
# Target unit tests
# ---------------------------------------------------------------------------


class TestTargetDefinitions:
    def test_pkl_source_target_alias(self):
        assert PklSourceTarget.alias == "pkl_source"

    def test_pkl_source_target_has_expected_core_fields(self):
        field_types = {f for f in PklSourceTarget.core_fields}
        assert PklSourceField in field_types
        assert PklProjectDirField in field_types
        assert PklSkipEvalCheckField in field_types

    def test_pkl_sources_target_alias(self):
        assert PklSourcesTarget.alias == "pkl_sources"

    def test_pkl_sources_target_generated_cls(self):
        assert PklSourcesTarget.generated_target_cls is PklSourceTarget

    def test_pkl_sources_target_copied_fields(self):
        assert PklSourcesTarget.copied_fields == COMMON_TARGET_FIELDS

    def test_pkl_sources_target_moved_fields(self):
        moved = set(PklSourcesTarget.moved_fields)
        assert PklProjectDirField in moved
        assert PklSkipEvalCheckField in moved

    def test_pkl_test_target_alias(self):
        assert PklTestTarget.alias == "pkl_test"

    def test_pkl_test_target_has_expected_core_fields(self):
        field_types = {f for f in PklTestTarget.core_fields}
        assert PklTestSourceField in field_types
        assert PklSkipTestField in field_types
        assert PklTestTimeoutField in field_types
        assert PklJunitReportsField in field_types
        assert PklExtraArgsField in field_types

    def test_pkl_tests_target_alias(self):
        assert PklTestsTarget.alias == "pkl_tests"

    def test_pkl_tests_target_generated_cls(self):
        assert PklTestsTarget.generated_target_cls is PklTestTarget

    def test_pkl_tests_target_copied_fields(self):
        assert PklTestsTarget.copied_fields == COMMON_TARGET_FIELDS

    def test_pkl_tests_target_moved_fields(self):
        moved = set(PklTestsTarget.moved_fields)
        assert PklSkipTestField in moved
        assert PklTestTimeoutField in moved
        assert PklProjectDirField in moved
        assert PklJunitReportsField in moved
        assert PklExtraArgsField in moved

    def test_pkl_package_target_alias(self):
        assert PklPackageTarget.alias == "pkl_package"

    def test_pkl_package_target_has_expected_core_fields(self):
        field_types = {f for f in PklPackageTarget.core_fields}
        assert PklSourceField in field_types
        assert PklOutputFormatField in field_types
        assert PklOutputPathField in field_types
        assert PklMultipleOutputField in field_types
        assert PklMultipleOutputPathField in field_types
        assert PklExpressionField in field_types
        assert PklModulePathField in field_types
        assert PklExtraArgsField in field_types


# ---------------------------------------------------------------------------
# RuleRunner integration tests — target generation
# ---------------------------------------------------------------------------


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        target_types=[
            PklSourceTarget,
            PklSourcesTarget,
            PklTestTarget,
            PklTestsTarget,
            PklPackageTarget,
        ],
    )


class TestRuleRunnerIntegration:
    def _all_targets(self, rule_runner: RuleRunner):
        """Return all targets known to the RuleRunner."""
        return rule_runner.request(AllTargets, [])

    def test_pkl_sources_generates_pkl_source_per_file(self, rule_runner: RuleRunner):
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='lib')\n",
                "src/config.pkl": "name = \"test\"\n",
                "src/utils.pkl": "port = 8080\n",
            }
        )
        all_targets = self._all_targets(rule_runner)
        # Filter to generated pkl_source targets in src/
        source_targets = [
            t for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "src"
        ]
        assert len(source_targets) == 2
        source_files = {t[PklSourceField].value for t in source_targets}
        assert source_files == {"config.pkl", "utils.pkl"}

    def test_pkl_tests_generates_pkl_test_per_file(self, rule_runner: RuleRunner):
        rule_runner.write_files(
            {
                "tests/BUILD": "pkl_tests(name='tests')\n",
                "tests/math_test.pkl": 'amends "pkl:test"\n',
                "tests/utils_test.pkl": 'amends "pkl:test"\n',
            }
        )
        all_targets = self._all_targets(rule_runner)
        test_targets = [
            t for t in all_targets
            if t.alias == "pkl_test" and t.address.spec_path == "tests"
        ]
        assert len(test_targets) == 2
        test_files = {t[PklTestSourceField].value for t in test_targets}
        assert test_files == {"math_test.pkl", "utils_test.pkl"}

    def test_field_validation_rejects_non_pkl_files(self, rule_runner: RuleRunner):
        """Sources generator should reject non-.pkl files."""
        rule_runner.write_files(
            {
                "bad/BUILD": "pkl_sources(name='bad', sources=['config.json'])\n",
                "bad/config.json": '{"name": "test"}\n',
            }
        )
        with pytest.raises(Exception):
            self._all_targets(rule_runner)

    def test_pkl_sources_default_excludes_test_files(self, rule_runner: RuleRunner):
        """pkl_sources default pattern should exclude test files."""
        rule_runner.write_files(
            {
                "src/BUILD": "pkl_sources(name='lib')\n",
                "src/config.pkl": "name = \"test\"\n",
                "src/math_test.pkl": 'amends "pkl:test"\n',
                "src/MathTest.pkl": 'amends "pkl:test"\n',
            }
        )
        all_targets = self._all_targets(rule_runner)
        source_targets = [
            t for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "src"
        ]
        source_files = {t[PklSourceField].value for t in source_targets}
        # Only config.pkl should be included; test files excluded by default glob
        assert "config.pkl" in source_files
        assert "math_test.pkl" not in source_files
        assert "MathTest.pkl" not in source_files

    def test_pkl_package_target_fields(self, rule_runner: RuleRunner):
        """pkl_package target stores all packaging fields correctly."""
        rule_runner.write_files(
            {
                "pkg/BUILD": (
                    "pkl_package(\n"
                    "  name='mypkg',\n"
                    "  source='config.pkl',\n"
                    "  output_format='yaml',\n"
                    "  output_path='out/config.yaml',\n"
                    "  expression='.name',\n"
                    ")\n"
                ),
                "pkg/config.pkl": "name = \"myapp\"\n",
            }
        )
        tgt = rule_runner.get_target(Address("pkg", target_name="mypkg"))
        assert tgt.alias == "pkl_package"
        assert tgt[PklOutputFormatField].value == "yaml"
        assert tgt[PklOutputPathField].value == "out/config.yaml"
        assert tgt[PklExpressionField].value == ".name"

    def test_moved_fields_propagated_to_generated_targets(self, rule_runner: RuleRunner):
        """Fields in moved_fields are propagated from generator to generated targets."""
        rule_runner.write_files(
            {
                "src/BUILD": (
                    "pkl_sources(\n"
                    "  name='lib',\n"
                    "  project_dir='myproject',\n"
                    "  skip_eval_check=True,\n"
                    ")\n"
                ),
                "src/config.pkl": "name = \"test\"\n",
            }
        )
        all_targets = self._all_targets(rule_runner)
        source_targets = [
            t for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "src"
        ]
        assert len(source_targets) == 1
        assert source_targets[0][PklProjectDirField].value == "myproject"
        assert source_targets[0][PklSkipEvalCheckField].value is True
