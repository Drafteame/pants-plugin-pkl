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
from pants.core.util_rules.external_tool import ExternalToolRequest, download_external_tool
from pants.core.util_rules.partitions import PartitionerType
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import MergeDigests, PathGlobs
from pants.engine.internals.selectors import concurrently
from pants.engine.internals.graph import transitive_targets
from pants.engine.intrinsics import execute_process, merge_digests, path_globs_to_digest
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    Dependencies,
    FieldSet,
    Target,
    TransitiveTargetsRequest,
)
from pkl.lint.eval_check.subsystem import PklEvalCheck
from pkl.pkl_process import PKL_PACKAGES_DIR, build_pkl_argv
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
    downloaded_pkl = await download_external_tool(pkl.get_request(platform))

    field_sets = request.elements

    # Collect per-file source digests.
    all_source_files = await concurrently(
        determine_source_files(SourceFilesRequest([fs.source]))
        for fs in field_sets
    )

    # Gather transitive dependencies so imports resolve inside the sandbox.
    transitive_targets_list = await concurrently(
        transitive_targets(**implicitly(TransitiveTargetsRequest([fs.address])))
        for fs in field_sets
    )
    dep_sources_list = await concurrently(
        determine_source_files(
            SourceFilesRequest(
                [tgt.get(PklSourceField) for tgt in tt.dependencies if tgt.has_field(PklSourceField)],
                for_sources_types=(PklSourceField,),
                enable_codegen=False,
            )
        )
        for tt in transitive_targets_list
    )

    # Include ALL PklProject, PklProject.deps.json, and vendored PKL packages
    # in the sandbox so pkl can resolve @-prefixed package aliases and external
    # package:// dependencies.
    all_pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json", f"{PKL_PACKAGES_DIR}/**"])
    )

    # Merge all digests: binary + per-file sources + dep sources + PklProject.
    input_digest = await merge_digests(
        MergeDigests(
            (
                downloaded_pkl.digest,
                *(sf.snapshot.digest for sf in all_source_files),
                *(sf.snapshot.digest for sf in dep_sources_list),
                all_pkl_project_digest,
            )
        )
    )

    # Run one eval process per source file, all sharing the merged sandbox.
    # `--format json -o /dev/null` evaluates the module and discards the output.
    # JSON is used instead of the default PCF renderer because PCF cannot render
    # Map values or certain PKL-typed objects.  JSON handles these cases while
    # still propagating all real evaluation errors (type mismatches, constraint
    # violations, unresolved imports, etc.).
    results = await concurrently(
        execute_process(
            **implicitly(
                Process(
                    argv=tuple(
                        build_pkl_argv(
                            downloaded_pkl.exe,
                            "eval",
                            fs.source.file_path,
                            project_dir=fs.project_dir.value,
                            extra_args=(*pkl_eval_check_subsystem.args, "--format", "json", "-o", "/dev/null"),
                            # Enable cache so external package:// dependencies resolve from
                            # the vendored pkl-packages/ directory (no network required).
                            use_cache=True,
                        )
                    ),
                    input_digest=input_digest,
                    description=f"Validate PKL module {fs.source.file_path}",
                )
            )
        )
        for fs in field_sets
    )

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
