"""Rules for the PKL eval-check lint goal.

Runs `pkl eval -x module -o /dev/null <source>` for each PKL source file
and reports a lint failure if the exit code is non-zero.  This catches type
errors, constraint violations, unresolved imports, and other evaluation-time
errors.

Using ``-x module`` (expression mode) bypasses the output renderer entirely,
avoiding two classes of false positives:

* PCF cannot render ``Map`` values or certain PKL-typed objects.
* JSON cannot render ``Class``-type values (e.g. ``fixed Type: Class = type``
  in third-party packages such as formae).

Despite skipping the renderer, ``-x module`` still forces full evaluation of
the module, so all real errors (type mismatches, constraint violations,
unresolved imports) are still caught.
"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.lint import LintResult, LintTargetsRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import MergeDigests, PathGlobs
from pants.engine.internals.selectors import concurrently
from pants.engine.internals.graph import transitive_targets
from pants.engine.intrinsics import digest_to_snapshot, execute_process, merge_digests, path_globs_to_digest
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    Dependencies,
    FieldSet,
    Target,
    TransitiveTargetsRequest,
)
from pkl.lint.eval_check.subsystem import PklEvalCheck
from pkl.pkl_dependencies import PklResolvedPackagesRequest, resolve_pkl_packages
from pkl.pkl_process import build_pkl_argv, detect_project_dir
from pkl.subsystem import PklBinaryRequest, resolve_pkl_binary
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
    pkl_eval_check_subsystem: PklEvalCheck,
) -> LintResult:
    # Resolve the pkl binary (system or downloaded).
    pkl_binary = await resolve_pkl_binary(PklBinaryRequest())

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

    # Include PklProject, PklProject.deps.json, and resolved PKL packages
    # in the sandbox so pkl can resolve @-prefixed package aliases and external
    # package:// dependencies.
    pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json"])
    )
    resolved_packages = await resolve_pkl_packages(PklResolvedPackagesRequest())
    all_pkl_project_digest = await merge_digests(
        MergeDigests((pkl_project_digest, resolved_packages.digest))
    )

    # Merge all digests: binary + per-file sources + dep sources + PklProject.
    input_digest = await merge_digests(
        MergeDigests(
            (
                pkl_binary.digest,
                *(sf.snapshot.digest for sf in all_source_files),
                *(sf.snapshot.digest for sf in dep_sources_list),
                all_pkl_project_digest,
            )
        )
    )

    # Build a snapshot so detect_project_dir can walk the sandbox file tree.
    sandbox_snapshot = await digest_to_snapshot(input_digest)

    # Run one eval process per source file, all sharing the merged sandbox.
    # `-x module -o /dev/null` evaluates the full module object and discards output.
    # Using `-x module` (expression mode) bypasses the output renderer entirely,
    # which avoids two classes of false positives:
    #   - PCF cannot render Map values or certain PKL-typed objects.
    #   - JSON cannot render Class-type values (e.g. `fixed Type: Class = type`
    #     in third-party packages like formae@0.82.1).
    # Despite skipping the renderer, `-x module` still forces full evaluation:
    # type mismatches, constraint violations, and import errors all produce
    # a non-zero exit code as expected.
    results = await concurrently(
        execute_process(
            **implicitly(
                Process(
                    argv=tuple(
                        build_pkl_argv(
                            pkl_binary.exe,
                            "eval",
                            fs.source.file_path,
                            project_dir=fs.project_dir.value or detect_project_dir(fs.source.file_path, frozenset(sandbox_snapshot.files)),
                            extra_args=(*pkl_eval_check_subsystem.args, "-x", "module", "-o", "/dev/null"),
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
