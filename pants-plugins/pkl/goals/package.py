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
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, MergeDigests, Snapshot
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import Dependencies, TransitiveTargets, TransitiveTargetsRequest
from pants.engine.unions import UnionRule

from pkl.pkl_process import build_pkl_argv
from pkl.subsystem import PklTool
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
    pkl: PklTool,
    platform: Platform,
) -> BuiltPackage:
    # 1. Download the pkl binary.
    downloaded_pkl = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        pkl.get_request(platform),
    )

    # 2. Gather sources.
    sources = await Get(SourceFiles, SourceFilesRequest([field_set.source]))

    # 3. Gather transitive dependencies so imports resolve inside the sandbox.
    transitive = await Get(
        TransitiveTargets, TransitiveTargetsRequest([field_set.address])
    )
    dep_sources = await Get(
        SourceFiles,
        SourceFilesRequest(
            [tgt.get(PklSourceField) for tgt in transitive.dependencies if tgt.has_field(PklSourceField)],
            for_sources_types=(PklSourceField,),
            enable_codegen=False,
        ),
    )

    # 4. Merge all input digests.
    input_digest = await Get(
        Digest,
        MergeDigests(
            (
                downloaded_pkl.digest,
                sources.snapshot.digest,
                dep_sources.snapshot.digest,
            )
        ),
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
            downloaded_pkl.exe,
            "eval",
            source_path,
            project_dir=field_set.project_dir.value,
            extra_args=tuple(extra),
        )

        process = Process(
            argv=tuple(argv),
            input_digest=input_digest,
            output_directories=(output_base,),
            description=f"Package {source_path} (multi-output)",
        )
        result = await Get(ProcessResult, Process, process)

        # Enumerate files written by PKL from the output digest.
        output_snapshot = await Get(Snapshot, Digest, result.output_digest)
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
            downloaded_pkl.exe,
            "eval",
            source_path,
            project_dir=field_set.project_dir.value,
            extra_args=tuple(extra),
        )

        process = Process(
            argv=tuple(argv),
            input_digest=input_digest,
            output_files=(out_path,),
            description=f"Package {source_path} as {field_set.output_format.value}",
        )
        result = await Get(ProcessResult, Process, process)
        artifacts = (BuiltPackageArtifact(out_path),)

    return BuiltPackage(result.output_digest, artifacts=artifacts)


def rules():
    return [
        *collect_rules(),
        UnionRule(PackageFieldSet, PklPackageFieldSet),
    ]
