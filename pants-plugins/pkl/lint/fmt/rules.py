"""Rules for the PKL formatter (`pkl format` via `pants fmt` / `pants lint`).

Important: `pkl format` does NOT accept `--no-cache`, `--color`, `--allowed-modules`, or
`--allowed-resources`.  Only `--root-dir .` is valid.  argv is therefore built manually here
rather than via `build_pkl_argv()`.

Requires PKL >= 0.30.0 (the `format` subcommand was introduced in that release).
"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.engine.fs import Digest, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import Process, ProcessResult
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pants.core.util_rules.partitions import PartitionerType

from pkl.lint.fmt.subsystem import PklFmt
from pkl.subsystem import PklTool
from pkl.target_types import PklSourceField


@dataclass(frozen=True)
class PklFmtFieldSet(FieldSet):
    required_fields = (PklSourceField,)

    source: PklSourceField


class PklFmtRequest(FmtTargetsRequest):
    field_set_type = PklFmtFieldSet
    tool_subsystem = PklFmt


@rule(desc="Format with pkl format", level=LogLevel.DEBUG)
async def pkl_fmt(
    request: PklFmtRequest.Batch,
    pkl: PklTool,
    pkl_fmt_subsystem: PklFmt,
    platform: Platform,
) -> FmtResult:
    downloaded_pkl = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        pkl.get_request(platform),
    )

    source_files = request.snapshot.files

    input_digest = await Get(
        Digest,
        MergeDigests((downloaded_pkl.digest, request.snapshot.digest)),
    )

    # pkl format does NOT accept the common sandbox flags (--no-cache, --color, etc.).
    # Only --root-dir and --write are valid here.
    argv = [
        downloaded_pkl.exe,
        "format",
        "--write",
        "--root-dir",
        ".",
        *pkl_fmt_subsystem.args,
        *source_files,
    ]

    process = Process(
        argv=tuple(argv),
        input_digest=input_digest,
        output_files=source_files,
        description=f"Run pkl format on {pluralize(len(source_files), 'file')}",
        level=LogLevel.DEBUG,
    )
    result = await Get(ProcessResult, Process, process)
    return await FmtResult.create(request, result)


def rules():
    return [
        *collect_rules(),
        *PklFmtRequest.rules(partitioner_type=PartitionerType.DEFAULT_SINGLE_PARTITION),
        UnionRule(FmtTargetsRequest, PklFmtRequest),
    ]
