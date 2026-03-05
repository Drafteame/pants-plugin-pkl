"""PKL target types and fields for the Pants PKL plugin."""

from pants.engine.rules import collect_rules
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    BoolField,
    Dependencies,
    IntField,
    MultipleSourcesField,
    OverridesField,
    SingleSourceField,
    StringField,
    StringSequenceField,
    Target,
    TargetFilesGenerator,
)

# ---------------------------------------------------------------------------
# Source fields
# ---------------------------------------------------------------------------


class PklSourceField(SingleSourceField):
    expected_file_extensions = (".pkl",)


class PklGeneratingSourcesField(MultipleSourcesField):
    expected_file_extensions = (".pkl",)
    default = ("*.pkl", "!*_test.pkl", "!*Test.pkl", "!test_*.pkl", "!PklProject")


class PklTestSourceField(SingleSourceField):
    expected_file_extensions = (".pkl",)


class PklTestGeneratingSourcesField(MultipleSourcesField):
    expected_file_extensions = (".pkl",)
    default = ("*_test.pkl", "*Test.pkl", "test_*.pkl")


# ---------------------------------------------------------------------------
# Configuration fields
# ---------------------------------------------------------------------------


class PklOutputFormatField(StringField):
    alias = "output_format"
    default = "json"
    help = "Output format: json, yaml, plist, properties, pcf, textproto, xml, jsonnet"
    valid_choices = ("json", "yaml", "plist", "properties", "pcf", "textproto", "xml", "jsonnet")


class PklOutputPathField(StringField):
    alias = "output_path"
    default = None
    help = "Output file path. Supports %{moduleName} and %{moduleDir} placeholders."


class PklMultipleOutputField(BoolField):
    alias = "multiple_outputs"
    default = False
    help = "Enable multiple file output (uses output.files from the PKL module)."


class PklMultipleOutputPathField(StringField):
    alias = "multiple_output_path"
    default = "."
    help = "Base directory for multiple file output."


class PklExpressionField(StringField):
    alias = "expression"
    default = None
    help = "Expression to evaluate within the module (instead of full output)."


class PklProjectDirField(StringField):
    alias = "project_dir"
    default = None
    help = "Path to the PklProject directory for dependency resolution."


class PklModulePathField(StringField):
    alias = "module_path"
    default = None
    help = "Directories/archives to search for modulepath: URIs."


class PklExtraArgsField(StringSequenceField):
    alias = "extra_args"
    default = ()
    help = "Extra arguments passed to the pkl command."


class PklSkipTestField(BoolField):
    alias = "skip_test"
    default = False
    help = "Skip testing this target."


class PklTestTimeoutField(IntField):
    alias = "timeout"
    default = None  # None = use subsystem default
    help = "Timeout in seconds for pkl test."


class PklJunitReportsField(BoolField):
    alias = "junit_reports"
    default = False
    help = "Generate JUnit XML reports."


class PklSkipEvalCheckField(BoolField):
    alias = "skip_eval_check"
    default = False
    help = "Skip pkl eval validation for this target."


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


class PklSourceTarget(Target):
    alias = "pkl_source"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        PklSourceField,
        Dependencies,
        PklProjectDirField,
        PklSkipEvalCheckField,
    )
    help = "A single PKL source file."


class PklSourcesTarget(TargetFilesGenerator):
    alias = "pkl_sources"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        PklGeneratingSourcesField,
        OverridesField,
    )
    generated_target_cls = PklSourceTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (Dependencies, PklProjectDirField, PklSkipEvalCheckField)
    help = "Generate a `pkl_source` target for each file in the `sources` field."


class PklTestTarget(Target):
    alias = "pkl_test"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        PklTestSourceField,
        Dependencies,
        PklSkipTestField,
        PklTestTimeoutField,
        PklProjectDirField,
        PklJunitReportsField,
        PklExtraArgsField,
    )
    help = "A PKL test module (extends pkl:test)."


class PklTestsTarget(TargetFilesGenerator):
    alias = "pkl_tests"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        PklTestGeneratingSourcesField,
        OverridesField,
    )
    generated_target_cls = PklTestTarget
    copied_fields = COMMON_TARGET_FIELDS
    moved_fields = (
        Dependencies,
        PklSkipTestField,
        PklTestTimeoutField,
        PklProjectDirField,
        PklJunitReportsField,
        PklExtraArgsField,
    )
    help = "Generate a `pkl_test` target for each file in the `sources` field."


class PklPackageTarget(Target):
    alias = "pkl_package"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        PklSourceField,
        Dependencies,
        PklOutputFormatField,
        PklOutputPathField,
        PklMultipleOutputField,
        PklMultipleOutputPathField,
        PklExpressionField,
        PklProjectDirField,
        PklModulePathField,
        PklExtraArgsField,
    )
    help = "A PKL module to evaluate and package into a config file (JSON, YAML, XML, etc)."


def rules():
    return collect_rules()
