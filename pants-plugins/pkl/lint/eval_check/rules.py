"""Rules for the PKL eval-check lint goal.

Runs `pkl eval --format json -o /dev/null <source>` for each PKL source file
and reports a lint failure if the exit code is non-zero.  This catches type
errors, constraint violations, unresolved imports, and other evaluation-time
errors.

Using ``--format json`` (rather than the default PCF renderer) avoids the
serialization error that occurs when the module outputs a ``Map`` or certain
PKL-typed values that PCF cannot handle.  JSON can render ``Map`` values and
most typed objects, while still propagating all real evaluation errors.
"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.partitions import PartitionerType
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
from pkl.lint.eval_check.subsystem import PklEvalCheck
from pkl.pkl_process import build_pkl_argv
from pkl.subsystem import PklTool
from pkl.target_types import PklProjectDirField, PklSkipEvalCheckField, PklSourceField


@dataclass(frozen=True)
class PklEvalCheckFieldSet(FieldSet):
    required_fields = (PklSourceField,)

    source: PklSourceField
    dependencies: Dependencies
    project_dir: PklProjectDirField
    skip_eval_check: PklSkipEvalCheckField

    @classmethod
    def opt_out(cls, tgt: Target) -> bool:
        return tgt.get(PklSkipEvalCheckField).value


class PklEvalCheckRequest(LintTargetsRequest):
    field_set_type = PklEvalCheckFieldSet
    tool_subsystem = PklEvalCheck
    partitioner_type = PartitionerType.DEFAULT_SINGLE_PARTITION


@rule(desc="Validate PKL source with pkl eval")
async def pkl_eval_check(
    request: PklEvalCheckRequest.Batch,
    pkl: PklTool,
    pkl_eval_check_subsystem: PklEvalCheck,
    platform: Platform,
) -> LintResult:
    downloaded_pkl = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        pkl.get_request(platform),
    )

    field_sets = request.elements

    # Collect per-file source digests.
    all_source_files = await MultiGet(
        Get(SourceFiles, SourceFilesRequest([fs.source])) for fs in field_sets
    )

    # Gather transitive dependencies so imports resolve inside the sandbox.
    transitive_targets_list = await MultiGet(
        Get(TransitiveTargets, TransitiveTargetsRequest([fs.address]))
        for fs in field_sets
    )
    dep_sources_list = await MultiGet(
        Get(
            SourceFiles,
            SourceFilesRequest(
                [tgt.get(PklSourceField) for tgt in tt.dependencies if tgt.has_field(PklSourceField)],
                for_sources_types=(PklSourceField,),
                enable_codegen=False,
            ),
        )
        for tt in transitive_targets_list
    )

    # Include ALL PklProject and PklProject.deps.json files so pkl can
    # resolve @-prefixed package aliases.
    all_pkl_project_snapshot = await Get(
        Snapshot, PathGlobs(["**/PklProject", "**/PklProject.deps.json"])
    )

    # Merge all digests: binary + per-file sources + dep sources + PklProject.
    input_digest = await Get(
        Digest,
        MergeDigests(
            (
                downloaded_pkl.digest,
                *(sf.snapshot.digest for sf in all_source_files),
                *(sf.snapshot.digest for sf in dep_sources_list),
                all_pkl_project_snapshot.digest,
            )
        ),
    )

    # Run one eval process per source file, all sharing the merged sandbox.
    # `--format json -o /dev/null` evaluates the module and discards the output.
    # JSON is used instead of the default PCF renderer because PCF cannot render
    # Map values or certain PKL-typed objects.  JSON handles these cases while
    # still propagating all real evaluation errors (type mismatches, constraint
    # violations, unresolved imports, etc.).
    results: list[FallibleProcessResult] = []
    for fs in field_sets:
        source_path = fs.source.file_path
        argv = build_pkl_argv(
            downloaded_pkl.exe,
            "eval",
            source_path,
            project_dir=fs.project_dir.value,
            extra_args=(*pkl_eval_check_subsystem.args, "--format", "json", "-o", "/dev/null"),
        )

        process = Process(
            argv=tuple(argv),
            input_digest=input_digest,
            description=f"Validate PKL module {source_path}",
        )
        result = await Get(FallibleProcessResult, Process, process)
        results.append(result)

    # Aggregate: any non-zero exit code → overall failure.
    exit_code = max((r.exit_code for r in results), default=0)
    stdout = b"\n".join(r.stdout for r in results)
    stderr = b"\n".join(r.stderr for r in results)

    return LintResult(
        exit_code=exit_code,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        linter_name=PklEvalCheckRequest.tool_subsystem.options_scope,
        partition_description=request.partition_metadata,
    )


def rules():
    return [
        *collect_rules(),
        *PklEvalCheckRequest.rules(),
    ]
