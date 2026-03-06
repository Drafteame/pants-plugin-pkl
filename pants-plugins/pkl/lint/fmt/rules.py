"""Rules for the PKL formatter (`pkl format` via `pants fmt` / `pants lint`).

Important: `pkl format` does NOT accept `--root-dir`, `--no-cache`, `--color`,
`--allowed-modules`, or `--allowed-resources`.  The only relevant flags are
``--write``, ``--diff-name-only``, ``--silent``, and ``--grammar-version``.
The argv is therefore built manually here rather than via ``build_pkl_argv()``,
which would include unsupported sandbox-containment flags.

Requires PKL >= 0.30.0 (the `format` subcommand was introduced in that release).
"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.fmt import FmtResult, FmtTargetsRequest
from pants.core.util_rules.partitions import PartitionerType
from pants.engine.fs import MergeDigests
from pants.engine.intrinsics import merge_digests
from pants.engine.process import execute_process_or_raise
from pants.engine.process import Process
from pants.engine.rules import Get, collect_rules, implicitly, rule
from pants.engine.target import FieldSet
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

from pkl.lint.fmt.subsystem import PklFmt
from pkl.subsystem import PklBinary, PklBinaryRequest, _version_gte
from pkl.target_types import PklSourceField


# `pkl format` was introduced in PKL 0.30.0. Earlier versions do not have
# the subcommand at all and will error with "Unknown command: format".
_PKL_FORMAT_MIN_VERSION = "0.30.0"


@dataclass(frozen=True)
class PklFmtFieldSet(FieldSet):
    required_fields = (PklSourceField,)

    source: PklSourceField


class PklFmtRequest(FmtTargetsRequest):
    field_set_type = PklFmtFieldSet
    tool_subsystem = PklFmt
    partitioner_type = PartitionerType.DEFAULT_SINGLE_PARTITION


@rule(desc="Format with pkl format", level=LogLevel.DEBUG)
async def pkl_fmt(
    request: PklFmtRequest.Batch,
    pkl_fmt_subsystem: PklFmt,
) -> FmtResult:
    # Resolve the pkl binary (system or downloaded).
    pkl_binary = await Get(PklBinary, PklBinaryRequest())

    # pkl format was introduced in PKL 0.30.0.
    if not _version_gte(pkl_binary.version, _PKL_FORMAT_MIN_VERSION):
        raise ValueError(
            f"pkl format requires PKL >= {_PKL_FORMAT_MIN_VERSION}, but the "
            f"resolved pkl binary is version {pkl_binary.version}. Either "
            f"upgrade your pkl installation, set [pkl].version to >= "
            f"{_PKL_FORMAT_MIN_VERSION}, or disable the pkl.lint.fmt backend."
        )

    source_files = request.snapshot.files

    input_digest = await merge_digests(
        MergeDigests((pkl_binary.digest, request.snapshot.digest))
    )

    # `pkl format` only accepts: --write, --diff-name-only, --silent, --grammar-version.
    # It does NOT accept --root-dir, --no-cache, --color, or any eval-style flags,
    # so we build argv directly rather than using build_pkl_argv().
    argv = [
        pkl_binary.exe,
        "format",
        "--write",
        *pkl_fmt_subsystem.args,
        *source_files,
    ]

    result = await execute_process_or_raise(
        **implicitly(
            Process(
                argv=tuple(argv),
                input_digest=input_digest,
                output_files=source_files,
                description=f"Run pkl format on {pluralize(len(source_files), 'file')}",
                level=LogLevel.DEBUG,
            )
        )
    )
    return await FmtResult.create(request, result)


def rules():
    return [
        *collect_rules(),
        *PklFmtRequest.rules(),
    ]
