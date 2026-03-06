"""PKL packaging goal — integrates `pkl eval` with `pants package`.

Each `pkl_package` target is evaluated with `pkl eval` and the output is
written to `dist/`.  Three output modes are supported:

* **Single-file** (default): `pkl eval --format <fmt> -o <path> <source>`
* **Multi-file**: `pkl eval -m <base_dir> <source>` — PKL's `output.files`
  mechanism writes multiple files; artifacts are enumerated from the digest.
* **Expression**: adds `-x <expr>` to single-file mode to evaluate a
  sub-expression instead of the whole module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath

from pants.core.goals.package import BuiltPackage, BuiltPackageArtifact, PackageFieldSet
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.fs import MergeDigests, PathGlobs
from pants.engine.internals.graph import transitive_targets
from pants.engine.intrinsics import (
    digest_to_snapshot,
    merge_digests,
    path_globs_to_digest,
)
from pants.engine.process import execute_process_or_raise
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import Dependencies, TransitiveTargetsRequest
from pants.engine.unions import UnionRule

from pkl.pkl_dependencies import PklResolvedPackagesRequest, resolve_pkl_packages
from pkl.pkl_process import build_pkl_argv
from pkl.subsystem import PklBinaryRequest, resolve_pkl_binary
from pkl.target_types import (
    PklExpressionField,
    PklExtraArgsField,
    PklModulePathField,
    PklMultipleOutputField,
    PklMultipleOutputPathField,
    PklOutputFormatField,
    PklOutputPathField,
    PklProjectDirField,
    PklSourceField,
)

# Maps PKL output_format values to file extensions.
FORMAT_EXTENSIONS: dict[str, str] = {
    "json": ".json",
    "yaml": ".yaml",
    "plist": ".plist",
    "properties": ".properties",
    "pcf": ".pcf",
    "textproto": ".textproto",
    "xml": ".xml",
    "jsonnet": ".jsonnet",
}


@dataclass(frozen=True)
class PklPackageFieldSet(PackageFieldSet):
    required_fields = (PklSourceField, PklOutputFormatField)

    source: PklSourceField
    output_format: PklOutputFormatField
    output_path: PklOutputPathField
    multiple_outputs: PklMultipleOutputField
    multiple_output_path: PklMultipleOutputPathField
    expression: PklExpressionField
    project_dir: PklProjectDirField
    module_path: PklModulePathField
    extra_args: PklExtraArgsField
    dependencies: Dependencies


@rule(desc="Package PKL module")
async def package_pkl(
    field_set: PklPackageFieldSet,
) -> BuiltPackage:
    # 1. Resolve the pkl binary (system or downloaded).
    pkl_binary = await resolve_pkl_binary(PklBinaryRequest())

    # 2. Gather sources.
    sources = await determine_source_files(SourceFilesRequest([field_set.source]))

    # 3. Gather transitive dependencies so imports resolve inside the sandbox.
    transitive = await transitive_targets(
        **implicitly(TransitiveTargetsRequest([field_set.address]))
    )
    dep_sources = await determine_source_files(
        SourceFilesRequest(
            [tgt.get(PklSourceField) for tgt in transitive.dependencies if tgt.has_field(PklSourceField)],
            for_sources_types=(PklSourceField,),
            enable_codegen=False,
        )
    )

    # Include PklProject, PklProject.deps.json, and resolved PKL packages
    # so pkl can resolve both local (@-prefixed) and remote (package://) deps.
    pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json"])
    )
    resolved_packages = await resolve_pkl_packages(PklResolvedPackagesRequest())
    all_pkl_project_digest = await merge_digests(
        MergeDigests((pkl_project_digest, resolved_packages.digest))
    )

    # 4. Merge all input digests.
    input_digest = await merge_digests(
        MergeDigests(
            (
                pkl_binary.digest,
                sources.snapshot.digest,
                dep_sources.snapshot.digest,
                all_pkl_project_digest,
            )
        )
    )

    # 5. Build extra args (shared between modes).
    extra: list[str] = list(field_set.extra_args.value or ())
    if field_set.module_path.value:
        extra.extend(["--module-path", field_set.module_path.value])

    source_path = field_set.source.file_path

    if field_set.multiple_outputs.value:
        # ----------------------------------------------------------------
        # Multi-file output mode: pkl eval -m <base_dir> <source>
        # ----------------------------------------------------------------
        output_base = field_set.multiple_output_path.value or "."
        extra.extend(["-m", output_base])

        argv = build_pkl_argv(
            pkl_binary.exe,
            "eval",
            source_path,
            project_dir=field_set.project_dir.value,
            extra_args=tuple(extra),
            use_cache=True,
        )

        result = await execute_process_or_raise(
            **implicitly(
                Process(
                    argv=tuple(argv),
                    input_digest=input_digest,
                    output_directories=(output_base,),
                    description=f"Package {source_path} (multi-output)",
                )
            )
        )

        # Enumerate files written by PKL from the output digest.
        output_snapshot = await digest_to_snapshot(result.output_digest)
        artifacts = tuple(BuiltPackageArtifact(f) for f in output_snapshot.files)
    else:
        # ----------------------------------------------------------------
        # Single-file output mode: pkl eval --format <fmt> [-x <expr>] -o <path> <source>
        # ----------------------------------------------------------------
        extra.extend(["--format", field_set.output_format.value])

        if field_set.expression.value:
            extra.extend(["-x", field_set.expression.value])

        if field_set.output_path.value:
            out_path = field_set.output_path.value
        else:
            ext = FORMAT_EXTENSIONS[field_set.output_format.value]
            out_path = PurePath(source_path).stem + ext

        extra.extend(["-o", out_path])

        argv = build_pkl_argv(
            pkl_binary.exe,
            "eval",
            source_path,
            project_dir=field_set.project_dir.value,
            extra_args=tuple(extra),
            use_cache=True,
        )

        result = await execute_process_or_raise(
            **implicitly(
                Process(
                    argv=tuple(argv),
                    input_digest=input_digest,
                    output_files=(out_path,),
                    description=f"Package {source_path} as {field_set.output_format.value}",
                )
            )
        )
        artifacts = (BuiltPackageArtifact(out_path),)

    return BuiltPackage(result.output_digest, artifacts=artifacts)


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, PklPackageFieldSet),
    ]
