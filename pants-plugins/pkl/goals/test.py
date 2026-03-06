"""PKL test runner — integrates `pkl test` with `pants test`.

Each `pkl_test` target is evaluated with `pkl test <source>`.  Exit codes:
  0  — all tests passed
  1  — one or more tests failed
  10 — only expected-file writes occurred (new snapshots created)

Note on batching: ``run_pkl_test`` is invoked with a single-element batch
(``batch.single_element``).  Pants guarantees this for ``TestRequest`` because
each PKL test module runs as its own isolated ``Process``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.goals.test import ShowOutput, TestFieldSet, TestRequest, TestResult
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import MergeDigests, PathGlobs
from pants.engine.internals.graph import transitive_targets
from pants.engine.intrinsics import (
    digest_to_snapshot,
    execute_process,
    merge_digests,
    path_globs_to_digest,
)
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, implicitly, rule
from pants.engine.target import (
    Dependencies,
    FieldSet,
    Target,
    TransitiveTargetsRequest,
)
from pants.option.option_types import ArgsListOption, BoolOption, IntOption, SkipOption
from pants.option.subsystem import Subsystem

from pkl.pkl_process import PKL_PACKAGES_DIR, build_pkl_argv
from pkl.subsystem import PklBinary, PklBinaryRequest
from pkl.target_types import (
    PklExtraArgsField,
    PklJunitReportsField,
    PklProjectDirField,
    PklSkipTestField,
    PklSourceField,
    PklTestSourceField,
    PklTestTimeoutField,
)


class PklTestSubsystem(Subsystem):
    options_scope = "pkl-test-runner"
    name = "pkl-test-runner"
    help = "Options for the PKL test runner (`pants test`)."

    skip = SkipOption("test")
    args = ArgsListOption(example="--overwrite")
    timeout_default = IntOption(
        default=0,
        help=(
            "Default timeout in seconds for each `pkl test` invocation. "
            "A value of 0 means no timeout. "
            "Can be overridden per-target with the `timeout` field."
        ),
        advanced=True,
    )
    overwrite = BoolOption(
        default=False,
        help=(
            "Pass `--overwrite` to `pkl test` to regenerate expected snapshot files "
            "(`.pkl-expected.pcf`)."
        ),
    )


@dataclass(frozen=True)
class PklTestFieldSet(TestFieldSet):
    required_fields = (PklTestSourceField,)

    source: PklTestSourceField
    dependencies: Dependencies
    timeout: PklTestTimeoutField
    skip: PklSkipTestField
    project_dir: PklProjectDirField
    junit_reports: PklJunitReportsField
    extra_args: PklExtraArgsField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return tgt.get(PklSkipTestField).value


class PklTestRequest(TestRequest):
    tool_subsystem = PklTestSubsystem
    field_set_type = PklTestFieldSet


@rule(desc="Run pkl test")
async def run_pkl_test(
    batch: PklTestRequest.Batch,
    pkl_test_subsystem: PklTestSubsystem,
) -> TestResult:
    field_set = batch.single_element
    source_path = field_set.source.file_path

    # 1. Resolve the pkl binary (system or downloaded).
    pkl_binary = await Get(PklBinary, PklBinaryRequest())

    # 2. Gather the test source file.
    sources = await determine_source_files(SourceFilesRequest([field_set.source]))

    # 3. Gather transitive dependencies (imported PKL modules).
    transitive = await transitive_targets(
        **implicitly(TransitiveTargetsRequest([field_set.address]))
    )
    dep_source_fields = [
        tgt.get(PklTestSourceField) for tgt in transitive.dependencies if tgt.has_field(PklTestSourceField)
    ] + [
        tgt.get(PklSourceField) for tgt in transitive.dependencies if tgt.has_field(PklSourceField)
    ]
    dep_sources = await determine_source_files(
        SourceFilesRequest(
            dep_source_fields,
            for_sources_types=(PklTestSourceField, PklSourceField),
            enable_codegen=False,
        )
    )

    # 4. Glob for snapshot expected files in the same directory.
    source_dir = os.path.dirname(source_path) or "."
    expected_glob = os.path.join(source_dir, "*.pkl-expected.pcf")
    expected_digest = await path_globs_to_digest(PathGlobs([expected_glob]))
    expected_snapshot = await digest_to_snapshot(expected_digest)

    # Include ALL PklProject, PklProject.deps.json, and vendored PKL packages
    # so pkl test can resolve both local (@-prefixed) and remote (package://) deps
    # without any network access.
    all_pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json", f"{PKL_PACKAGES_DIR}/**"])
    )

    # 5. Merge all digests.
    input_digest = await merge_digests(
        MergeDigests(
            (
                pkl_binary.digest,
                sources.snapshot.digest,
                dep_sources.snapshot.digest,
                expected_snapshot.digest,
                all_pkl_project_digest,
            )
        )
    )

    # 6. Build extra pre-positional flags.
    # All optional flags (--junit-reports, --overwrite) must appear BEFORE the
    # positional source-path argument.  We collect them into `pre_args` and pass
    # them via `extra_args` so that `build_pkl_argv` inserts them in the correct
    # position (after project-dir, before positional args).  This avoids the
    # fragile `argv.insert(-1, ...)` pattern which silently breaks when there is
    # more than one positional argument.
    pre_args: list[str] = list(field_set.extra_args.value or ())

    if field_set.junit_reports.value:
        pre_args.extend(["--junit-reports", ".junit"])

    if pkl_test_subsystem.overwrite:
        pre_args.append("--overwrite")

    argv = build_pkl_argv(
        pkl_binary.exe,
        "test",
        source_path,
        project_dir=field_set.project_dir.value,
        extra_args=tuple(pre_args),
        use_cache=True,
    )

    # 7. Determine timeout: per-target field wins, then subsystem default (0 = no timeout).
    timeout_seconds = field_set.timeout.value or pkl_test_subsystem.timeout_default or None

    # 8. Output directories: source dir captures .pkl-actual.pcf / .pkl-expected.pcf files.
    output_dirs = [source_dir]
    if field_set.junit_reports.value:
        output_dirs.append(".junit")

    result = await execute_process(
        **implicitly(
            Process(
                argv=tuple(argv),
                input_digest=input_digest,
                output_directories=tuple(output_dirs),
                description=f"Run pkl test on {source_path}",
                timeout_seconds=timeout_seconds,
            )
        )
    )

    return TestResult.from_fallible_process_result(
        (result,),
        address=field_set.address,
        output_setting=ShowOutput.ALL,
    )


def rules():
    return [
        *collect_rules(),
        *PklTestRequest.rules(),
    ]
