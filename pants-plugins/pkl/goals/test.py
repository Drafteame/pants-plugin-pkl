"""PKL test runner — integrates `pkl test` with `pants test`.

Each `pkl_test` target is evaluated with `pkl test <source>`.  Exit codes:
  0  — all tests passed
  1  — one or more tests failed
  10 — only expected-file writes occurred (new snapshots created)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pants.core.goals.test import ShowOutput, TestFieldSet, TestRequest, TestResult
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests, PathGlobs, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    Dependencies,
    FieldSet,
    Target,
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants.option.option_types import ArgsListOption, BoolOption, IntOption, SkipOption
from pants.option.subsystem import Subsystem

from pkl.pkl_process import build_pkl_argv
from pkl.subsystem import PklTool
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
    options_scope = "pkl-test"
    name = "pkl-test"
    help = "Options for the PKL test runner (`pants test`)."

    skip = SkipOption("test")
    args = ArgsListOption(example="--overwrite")
    timeout_default = IntOption(
        default=None,
        help=(
            "Default timeout in seconds for each `pkl test` invocation. "
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
    pkl: PklTool,
    pkl_test_subsystem: PklTestSubsystem,
    platform: Platform,
) -> TestResult:
    field_set = batch.single_element
    source_path = field_set.source.file_path

    # 1. Download the pkl binary.
    downloaded_pkl = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        pkl.get_request(platform),
    )

    # 2. Gather the test source file.
    sources = await Get(SourceFiles, SourceFilesRequest([field_set.source]))

    # 3. Gather transitive dependencies (imported PKL modules).
    transitive = await Get(
        TransitiveTargets, TransitiveTargetsRequest([field_set.address])
    )
    dep_sources = await Get(
        SourceFiles,
        SourceFilesRequest(
            transitive.dependencies,
            for_sources_types=(PklTestSourceField, PklSourceField),
            enable_codegen=False,
        ),
    )

    # 4. Glob for snapshot expected files in the same directory.
    source_dir = os.path.dirname(source_path) or "."
    expected_glob = os.path.join(source_dir, "*.pkl-expected.pcf")
    expected_snapshot = await Get(Snapshot, PathGlobs([expected_glob]))

    # 5. Merge all digests.
    input_digest = await Get(
        Digest,
        MergeDigests(
            (
                downloaded_pkl.digest,
                sources.snapshot.digest,
                dep_sources.snapshot.digest,
                expected_snapshot.digest,
            )
        ),
    )

    # 6. Build argv using the shared helper.
    argv = build_pkl_argv(
        downloaded_pkl.exe,
        "test",
        source_path,
        project_dir=field_set.project_dir.value,
        extra_args=tuple(field_set.extra_args.value or ()),
    )

    # Append JUnit flag before the source argument (before *args positional params).
    if field_set.junit_reports.value:
        # Insert before the last element (the source path) so it comes before positional args.
        argv.insert(-1, "--junit-reports")
        argv.insert(-1, ".junit")

    if pkl_test_subsystem.overwrite:
        argv.insert(-1, "--overwrite")

    # 7. Determine timeout: per-target field wins, then subsystem default.
    timeout_seconds = field_set.timeout.value or pkl_test_subsystem.timeout_default

    # 8. Output directories: source dir captures .pkl-actual.pcf / .pkl-expected.pcf files.
    output_dirs = [source_dir]
    if field_set.junit_reports.value:
        output_dirs.append(".junit")

    process = Process(
        argv=tuple(argv),
        input_digest=input_digest,
        output_directories=tuple(output_dirs),
        description=f"Run pkl test on {source_path}",
        timeout_seconds=timeout_seconds,
    )
    result = await Get(FallibleProcessResult, Process, process)

    return TestResult.from_fallible_process_result(
        result,
        address=field_set.address,
        output_setting=ShowOutput.ALL,
    )


def rules():
    return [
        *collect_rules(),
        *PklTestRequest.rules(),
    ]
