"""End-to-end integration test for the PKL Pants plugin (T14).

Exercises all implemented goals — target listing, eval-check lint, formatting,
test running, packaging, and dependency inference — from a single shared
mini-project written into a RuleRunner sandbox.

The test class ``TestPklPluginEndToEnd`` sets up a small but realistic project:

    src/
      BUILD           # pkl_sources, pkl_tests, pkl_package
      config.pkl      # standalone config module
      lib.pkl         # shared library module
      main.pkl        # imports lib.pkl  — exercises dep inference
      app_test.pkl    # amends "pkl:test"  — exercises test runner

Each test method drives one goal in isolation so failures are clearly scoped.
All tests require network access on first run to download the pkl binary.
"""

from __future__ import annotations

import pytest

from pants.core.goals.fmt import FmtResult
from pants.core.goals.lint import LintResult
from pants.core.goals.package import BuiltPackage
from pants.core.goals.tailor import (
    PutativeTargets,
    rules as tailor_rules,
)
from pants.core.goals.test import TestResult
from pants.core.util_rules.external_tool import rules as external_tool_rules
from pants.core.util_rules.source_files import rules as source_files_rules
from pants.engine.addresses import Address
from pants.engine.fs import Digest, PathGlobs, Snapshot
from pants.engine.rules import QueryRule
from pants.engine.target import AllTargets, AllUnexpandedTargets, InferredDependencies
from pants.testutil.rule_runner import RuleRunner

from pkl import register as pkl_register
from pkl.dependency_inference import (
    InferPklDependenciesRequest,
    PklInferenceFieldSet,
    rules as dep_inf_rules,
)
from pkl.goals import package as package_module
from pkl.goals import tailor as tailor_module
from pkl.goals import test as test_module
from pkl.goals.package import PklPackageFieldSet
from pkl.goals.tailor import PutativePklTargetsRequest
from pkl.goals.test import PklTestFieldSet, PklTestRequest
from pkl.lint.eval_check import register as eval_check_register
from pkl.lint.eval_check.rules import PklEvalCheckFieldSet, PklEvalCheckRequest
from pkl.lint.fmt import register as fmt_register
from pkl.lint.fmt.rules import PklFmtFieldSet, PklFmtRequest
from pkl.target_types import (
    PklSourceField,
    PklTestSourceField,
)

# ---------------------------------------------------------------------------
# Shared PKL source fixtures
# ---------------------------------------------------------------------------

_CONFIG_PKL = """\
name = "myapp"
port = 8080
debug = false
"""

_LIB_PKL = """\
greeting = "hello from lib"
"""

# main.pkl imports lib.pkl — exercises dependency inference
_MAIN_PKL = """\
import "lib.pkl"

result = lib.greeting
"""

# app_test.pkl is a PKL test module — filename matches "*_test.pkl" pattern
_APP_TEST_PKL = """\
amends "pkl:test"

facts {
  ["addition passes"] {
    1 + 1 == 2
  }
}
"""

# BUILD declares sources, tests, and a package target all in one directory
_BUILD_FILE = """\
pkl_sources(name="src")
pkl_tests(name="tests")
pkl_package(name="config_pkg", source="config.pkl", output_format="json")
"""


# ---------------------------------------------------------------------------
# Shared RuleRunner factory — function-scoped to keep tests independent
# ---------------------------------------------------------------------------


def _make_rule_runner() -> RuleRunner:
    """Create a full-stack RuleRunner with every PKL goal and lint backend registered."""
    return RuleRunner(
        rules=[
            # Core PKL rules (subsystem, target types, dep inference)
            *pkl_register.rules(),
            # Goal rules
            *test_module.rules(),
            *package_module.rules(),
            *tailor_module.rules(),
            # Lint backends
            *eval_check_register.rules(),
            *fmt_register.rules(),
            # Infrastructure
            *source_files_rules(),
            *external_tool_rules(),
            *dep_inf_rules(),
            *tailor_rules(),
            # Query rules for direct rule invocation
            QueryRule(AllTargets, []),
            QueryRule(AllUnexpandedTargets, []),
            QueryRule(LintResult, [PklEvalCheckRequest.Batch]),
            QueryRule(FmtResult, [PklFmtRequest.Batch]),
            QueryRule(TestResult, [PklTestRequest.Batch]),
            QueryRule(BuiltPackage, [PklPackageFieldSet]),
            QueryRule(InferredDependencies, [InferPklDependenciesRequest]),
            QueryRule(PutativeTargets, [PutativePklTargetsRequest]),
            QueryRule(Snapshot, [PathGlobs]),
            QueryRule(Snapshot, [Digest]),
        ],
        target_types=pkl_register.target_types(),
    )


def _write_mini_project(rule_runner: RuleRunner) -> None:
    """Write the shared mini-project files into the RuleRunner sandbox."""
    rule_runner.write_files(
        {
            "src/BUILD": _BUILD_FILE,
            "src/config.pkl": _CONFIG_PKL,
            "src/lib.pkl": _LIB_PKL,
            "src/main.pkl": _MAIN_PKL,
            "src/app_test.pkl": _APP_TEST_PKL,
        }
    )


# ---------------------------------------------------------------------------
# 1. Target listing — all expected targets are resolvable
# ---------------------------------------------------------------------------


class TestListTargets:
    """pants list src:: — all targets resolve with correct aliases and source fields."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_resolves_source_generator_and_leaves(self, rule_runner: RuleRunner) -> None:
        """pkl_sources generates one pkl_source leaf per matching .pkl file."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        # AllTargets replaces generators with their leaves; use AllUnexpandedTargets to
        # see the generator targets themselves.
        unexpanded = rule_runner.request(AllUnexpandedTargets, [])
        unexpanded_src = [t for t in unexpanded if t.address.spec_path == "src"]
        unexpanded_aliases = {t.alias for t in unexpanded_src}
        assert "pkl_sources" in unexpanded_aliases

        # AllTargets only returns the generated leaf targets.
        all_targets = rule_runner.request(AllTargets, [])
        src_targets = [t for t in all_targets if t.address.spec_path == "src"]
        leaf_aliases = {t.alias for t in src_targets}
        assert "pkl_source" in leaf_aliases

        # config.pkl, lib.pkl, main.pkl — app_test.pkl is excluded by the default
        # PklGeneratingSourcesField pattern ("!*_test.pkl")
        source_targets = [t for t in src_targets if t.alias == "pkl_source"]
        source_files = {t[PklSourceField].value for t in source_targets}
        assert source_files == {"config.pkl", "lib.pkl", "main.pkl"}

    def test_resolves_test_generator_and_leaves(self, rule_runner: RuleRunner) -> None:
        """pkl_tests generates one pkl_test leaf per matching test file."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        # Generator target visible in AllUnexpandedTargets.
        unexpanded = rule_runner.request(AllUnexpandedTargets, [])
        unexpanded_src = [t for t in unexpanded if t.address.spec_path == "src"]
        unexpanded_aliases = {t.alias for t in unexpanded_src}
        assert "pkl_tests" in unexpanded_aliases

        # Leaf targets visible in AllTargets.
        all_targets = rule_runner.request(AllTargets, [])
        src_targets = [t for t in all_targets if t.address.spec_path == "src"]
        leaf_aliases = {t.alias for t in src_targets}
        assert "pkl_test" in leaf_aliases

        # app_test.pkl matches "*_test.pkl"
        test_targets = [t for t in src_targets if t.alias == "pkl_test"]
        test_files = {t[PklTestSourceField].value for t in test_targets}
        assert test_files == {"app_test.pkl"}

    def test_resolves_package_target(self, rule_runner: RuleRunner) -> None:
        """pkl_package target is resolvable by address."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        tgt = rule_runner.get_target(Address("src", target_name="config_pkg"))
        assert tgt.alias == "pkl_package"


# ---------------------------------------------------------------------------
# 2. Eval-check lint
# ---------------------------------------------------------------------------


class TestEvalCheckLint:
    """pants lint src:: — eval check passes on valid PKL; fails on invalid PKL."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_valid_sources_pass(self, rule_runner: RuleRunner) -> None:
        """All well-formed PKL source files in the mini-project evaluate without errors."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        field_sets = tuple(
            PklEvalCheckFieldSet.create(t)
            for t in all_targets
            if t.alias == "pkl_source"
            and t.address.spec_path == "src"
            and not PklEvalCheckFieldSet.opt_out(t)
        )
        batch = PklEvalCheckRequest.Batch("", field_sets, partition_metadata=None)
        result = rule_runner.request(LintResult, [batch])

        assert result.exit_code == 0, (
            f"Expected eval-check to pass.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_invalid_pkl_fails(self, rule_runner: RuleRunner) -> None:
        """A type error in a PKL file causes the lint check to fail."""
        rule_runner.write_files(
            {
                "bad/BUILD": "pkl_sources(name='bad')\n",
                "bad/broken.pkl": "name: String = 42\n",
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        field_sets = tuple(
            PklEvalCheckFieldSet.create(t)
            for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "bad"
        )
        batch = PklEvalCheckRequest.Batch("", field_sets, partition_metadata=None)
        result = rule_runner.request(LintResult, [batch])

        assert result.exit_code != 0, (
            "Expected eval-check to fail on a PKL file with a type error."
        )

    def test_skip_eval_check_opts_out(self, rule_runner: RuleRunner) -> None:
        """Sources with skip_eval_check=True are excluded from the batch via opt_out."""
        rule_runner.write_files(
            {
                "abstract/BUILD": "pkl_sources(name='src', skip_eval_check=True)\n",
                "abstract/module.pkl": "abstract module\n",
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        abstract_targets = [
            t for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "abstract"
        ]
        assert len(abstract_targets) == 1
        assert PklEvalCheckFieldSet.opt_out(abstract_targets[0]) is True


# ---------------------------------------------------------------------------
# 3. Formatter
# ---------------------------------------------------------------------------


class TestFormatter:
    """pants fmt src:: — already-formatted files show no change; messy files do."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_well_formatted_files_unchanged(self, rule_runner: RuleRunner) -> None:
        """config.pkl, lib.pkl, and main.pkl are all well-formatted — no changes expected."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        field_sets = tuple(
            PklFmtFieldSet.create(t)
            for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "src"
        )
        snapshot = rule_runner.request(Snapshot, [PathGlobs(["src/*.pkl"])])
        batch = PklFmtRequest.Batch("", field_sets, partition_metadata=None, snapshot=snapshot)
        result = rule_runner.request(FmtResult, [batch])

        assert result.did_change is False, (
            "Formatter should not change already well-formatted PKL files."
        )

    def test_unformatted_file_is_changed(self, rule_runner: RuleRunner) -> None:
        """A PKL file missing spaces around `=` is reformatted by pkl format."""
        rule_runner.write_files(
            {
                "messy/BUILD": "pkl_sources(name='src')\n",
                "messy/messy.pkl": 'name="hello"\nage=30\n',
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        field_sets = tuple(
            PklFmtFieldSet.create(t)
            for t in all_targets
            if t.alias == "pkl_source" and t.address.spec_path == "messy"
        )
        snapshot = rule_runner.request(Snapshot, [PathGlobs(["messy/*.pkl"])])
        batch = PklFmtRequest.Batch("", field_sets, partition_metadata=None, snapshot=snapshot)
        result = rule_runner.request(FmtResult, [batch])

        assert result.did_change is True, (
            "Formatter should have detected formatting violations in messy.pkl."
        )


# ---------------------------------------------------------------------------
# 4. Test runner
# ---------------------------------------------------------------------------


class TestRunner:
    """pants test — passing and failing PKL tests produce the correct exit codes."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_passing_test_exits_zero(self, rule_runner: RuleRunner) -> None:
        """A PKL test with all true assertions exits 0."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        # app_test.pkl is generated from pkl_tests(name="tests"); its generated
        # address uses the file path as relative_file_path.
        all_targets = rule_runner.request(AllTargets, [])
        test_target = next(
            (
                t for t in all_targets
                if t.alias == "pkl_test"
                and t.address.spec_path == "src"
                and t[PklTestSourceField].value == "app_test.pkl"
            ),
            None,
        )
        assert test_target is not None, "Could not find the app_test.pkl test target"

        field_set = PklTestFieldSet.create(test_target)
        batch = PklTestRequest.Batch("", (field_set,), partition_metadata=None)
        result = rule_runner.request(TestResult, [batch])

        assert result.exit_code == 0, (
            f"Expected passing PKL test to exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_failing_test_exits_nonzero(self, rule_runner: RuleRunner) -> None:
        """A PKL test with a false assertion exits non-zero."""
        rule_runner.write_files(
            {
                "fail/BUILD": "pkl_test(name='bad', source='failing_test.pkl')\n",
                "fail/failing_test.pkl": (
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

        tgt = rule_runner.get_target(Address("fail", target_name="bad"))
        field_set = PklTestFieldSet.create(tgt)
        batch = PklTestRequest.Batch("", (field_set,), partition_metadata=None)
        result = rule_runner.request(TestResult, [batch])

        assert result.exit_code != 0, (
            "Expected failing PKL test to produce a non-zero exit code."
        )


# ---------------------------------------------------------------------------
# 5. Packaging
# ---------------------------------------------------------------------------


class TestPackaging:
    """pants package — produces correctly-named artifacts in the expected format."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_json_package_produces_artifact(self, rule_runner: RuleRunner) -> None:
        """pkl_package with output_format='json' produces a .json artifact."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        tgt = rule_runner.get_target(Address("src", target_name="config_pkg"))
        field_set = PklPackageFieldSet.create(tgt)
        result = rule_runner.request(BuiltPackage, [field_set])

        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "config.json"

    def test_yaml_package_produces_artifact(self, rule_runner: RuleRunner) -> None:
        """pkl_package with output_format='yaml' produces a .yaml artifact."""
        rule_runner.write_files(
            {
                "yaml_pkg/config.pkl": _CONFIG_PKL,
                "yaml_pkg/BUILD": (
                    'pkl_package(name="pkg", source="config.pkl", output_format="yaml")\n'
                ),
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        tgt = rule_runner.get_target(Address("yaml_pkg", target_name="pkg"))
        field_set = PklPackageFieldSet.create(tgt)
        result = rule_runner.request(BuiltPackage, [field_set])

        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "config.yaml"

    def test_custom_output_path(self, rule_runner: RuleRunner) -> None:
        """When output_path is specified, the artifact has that relpath."""
        rule_runner.write_files(
            {
                "custom_path/config.pkl": _CONFIG_PKL,
                "custom_path/BUILD": (
                    'pkl_package(\n'
                    '  name="pkg",\n'
                    '  source="config.pkl",\n'
                    '  output_format="json",\n'
                    '  output_path="out/app.json",\n'
                    ')\n'
                ),
            }
        )
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        tgt = rule_runner.get_target(Address("custom_path", target_name="pkg"))
        field_set = PklPackageFieldSet.create(tgt)
        result = rule_runner.request(BuiltPackage, [field_set])

        assert len(result.artifacts) == 1
        assert result.artifacts[0].relpath == "out/app.json"


# ---------------------------------------------------------------------------
# 6. Dependency inference
# ---------------------------------------------------------------------------


class TestDependencyInference:
    """pants dependencies — main.pkl importing lib.pkl yields an inferred dep."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_main_depends_on_lib(self, rule_runner: RuleRunner) -> None:
        """Dependency inference identifies that main.pkl imports lib.pkl."""
        _write_mini_project(rule_runner)
        rule_runner.set_options([], env_inherit={"PATH", "PYENV_ROOT", "HOME"})

        all_targets = rule_runner.request(AllTargets, [])
        main_target = next(
            (
                t for t in all_targets
                if t.alias == "pkl_source"
                and t.address.spec_path == "src"
                and t[PklSourceField].value == "main.pkl"
            ),
            None,
        )
        assert main_target is not None, "Could not locate the src/main.pkl target"

        field_set = PklInferenceFieldSet.create(main_target)
        request = InferPklDependenciesRequest(field_set)
        inferred = rule_runner.request(InferredDependencies, [request])

        inferred_specs = [str(addr) for addr in inferred.include]
        assert any("lib" in s for s in inferred_specs), (
            f"Expected main.pkl to infer a dependency on lib.pkl. Got: {inferred_specs}"
        )


# ---------------------------------------------------------------------------
# 7. Tailor
# ---------------------------------------------------------------------------


class TestTailor:
    """pants tailor — unowned .pkl files receive BUILD target suggestions."""

    @pytest.fixture
    def rule_runner(self) -> RuleRunner:
        return _make_rule_runner()

    def test_suggests_sources_and_tests_for_unowned_files(
        self, rule_runner: RuleRunner
    ) -> None:
        """Unowned source and test .pkl files both get target suggestions."""
        rule_runner.write_files(
            {
                "unowned/config.pkl": _CONFIG_PKL,
                "unowned/check_test.pkl": (
                    'amends "pkl:test"\nfacts { ["ok"] { true } }\n'
                ),
            }
        )
        result = rule_runner.request(
            PutativeTargets,
            [PutativePklTargetsRequest(("unowned",))],
        )
        aliases = {pt.type_alias for pt in result}
        assert "pkl_sources" in aliases, f"Expected pkl_sources suggestion, got: {aliases}"
        assert "pkl_tests" in aliases, f"Expected pkl_tests suggestion, got: {aliases}"

    def test_no_suggestions_for_owned_files(self, rule_runner: RuleRunner) -> None:
        """Files already claimed by a BUILD target are not re-suggested."""
        _write_mini_project(rule_runner)
        result = rule_runner.request(
            PutativeTargets,
            [PutativePklTargetsRequest(("src",))],
        )
        assert len(result) == 0, (
            f"Expected no tailor suggestions for already-owned src/ files, got: {result}"
        )
