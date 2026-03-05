"""Automatic dependency inference for PKL files.

Primary method: `pkl analyze imports -f json` — static analysis that extracts
all imports (including `import*`, `amends`, `extends`) without evaluating the
module.

Fallback method: regex over the source text — handles simple cases when the
primary method fails or for fast mode.

Both methods produce relative paths that are matched against known PKL source
targets to produce ``Address`` values.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pants.core.util_rules.external_tool import DownloadedExternalTool, ExternalToolRequest
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.addresses import Address
from pants.engine.fs import Digest, DigestContents, MergeDigests
from pants.engine.platform import Platform
from pants.engine.process import FallibleProcessResult, Process
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import (
    AllTargets,
    Dependencies,
    FieldSet,
    InferDependenciesRequest,
    InferredDependencies,
)
from pants.engine.unions import UnionRule

from pkl.pkl_process import build_pkl_argv
from pkl.subsystem import PklTool
from pkl.target_types import PklProjectDirField, PklSourceField

# ---------------------------------------------------------------------------
# Regex fallback parser
# ---------------------------------------------------------------------------

#: Matches PKL import-like statements: ``import``, ``import*``, ``amends``,
#: ``extends``, followed by a double-quoted string literal.
PKL_IMPORT_RE = re.compile(
    r'^\s*(?:import\*?|amends|extends)\s+"([^"]+)"',
    re.MULTILINE,
)

#: URI schemes that do NOT correspond to local files and should be ignored.
_IGNORED_SCHEMES = frozenset({"pkl", "package", "https", "http", "modulepath", "projectpackage"})


def _extract_local_paths_from_regex(source_text: str, source_file: str) -> list[str]:
    """Return sandbox-relative paths implied by the source text using the regex fallback.

    Args:
        source_text: Raw PKL source text.
        source_file: Sandbox-relative path of the source file being analysed
            (used to resolve relative imports).

    Returns:
        List of sandbox-relative paths for *local* imports only.
    """
    source_dir = str(PurePosixPath(source_file).parent)
    paths: list[str] = []
    for m in PKL_IMPORT_RE.finditer(source_text):
        uri = m.group(1)
        parsed = urlparse(uri)
        # Skip URIs with a known non-local scheme.
        if parsed.scheme in _IGNORED_SCHEMES:
            continue
        # Skip bare scheme-less URIs that look like package references (contain colon).
        if ":" in uri and parsed.scheme not in ("", "file"):
            continue
        if parsed.scheme == "file":
            # Absolute file:// URI — not easily resolvable in regex mode; skip.
            continue
        # Treat as a path relative to the importing file's directory.
        resolved = str(PurePosixPath(source_dir) / uri)
        # Normalize away ".." components.
        resolved = str(PurePosixPath(resolved))
        paths.append(resolved)
    return paths


# ---------------------------------------------------------------------------
# JSON output parsing
# ---------------------------------------------------------------------------


def _parse_analyze_output(json_bytes: bytes, source_file: str) -> list[str]:
    """Parse ``pkl analyze imports -f json`` output into sandbox-relative import paths.

    The JSON format is::

        {
          "imports": {
            "file:///abs/path/to/source.pkl": [
              {"uri": "file:///abs/path/to/dep.pkl"}
            ]
          }
        }

    We locate the entry whose key ends with ``source_file`` and return the
    relative paths of its direct imports.

    Args:
        json_bytes: Raw stdout from ``pkl analyze imports -f json``.
        source_file: Sandbox-relative path of the file whose deps we want.

    Returns:
        List of sandbox-relative paths (best-effort) for direct imports.
    """
    try:
        data = json.loads(json_bytes)
    except json.JSONDecodeError:
        return []

    imports_map: dict[str, list[dict]] = data.get("imports", {})

    # Find the entry for our source file.  PKL returns absolute file:// URIs.
    source_stem = source_file.lstrip("/")
    source_deps: list[dict] = []
    for uri, deps in imports_map.items():
        parsed = urlparse(uri)
        if parsed.scheme == "file" and parsed.path.lstrip("/").endswith(source_stem):
            source_deps = deps
            break

    paths: list[str] = []
    for dep in source_deps:
        dep_uri = dep.get("uri", "")
        parsed = urlparse(dep_uri)
        if parsed.scheme != "file":
            continue
        abs_path = parsed.path  # /private/var/folders/.../subdir/dep.pkl
        # Keep only the portion starting from source_stem's directory or just
        # use the last N components.  We look for the longest suffix that ends
        # in a .pkl file and try to match against known targets below.
        paths.append(abs_path.lstrip("/"))

    return paths


# ---------------------------------------------------------------------------
# Field set & request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PklInferenceFieldSet(FieldSet):
    required_fields = (PklSourceField,)

    source: PklSourceField
    dependencies: Dependencies
    project_dir: PklProjectDirField


class InferPklDependenciesRequest(InferDependenciesRequest):
    infer_from = PklInferenceFieldSet


# ---------------------------------------------------------------------------
# Inference rule
# ---------------------------------------------------------------------------


@rule(desc="Infer PKL dependencies via pkl analyze imports")
async def infer_pkl_dependencies(
    request: InferPklDependenciesRequest,
    pkl: PklTool,
    platform: Platform,
    all_targets: AllTargets,
) -> InferredDependencies:
    field_set = request.field_set

    # Download the pkl binary.
    downloaded_pkl = await Get(
        DownloadedExternalTool,
        ExternalToolRequest,
        pkl.get_request(platform),
    )

    # Get the source file.
    sources = await Get(SourceFiles, SourceFilesRequest([field_set.source]))
    if not sources.snapshot.files:
        return InferredDependencies([])

    source_file = sources.snapshot.files[0]

    # Merge binary + source into sandbox.
    input_digest = await Get(
        Digest,
        MergeDigests((downloaded_pkl.digest, sources.snapshot.digest)),
    )

    # Run `pkl analyze imports -f json <source>`.
    argv = build_pkl_argv(
        downloaded_pkl.exe,
        ("analyze", "imports"),
        "-f", "json",
        source_file,
        project_dir=field_set.project_dir.value,
    )

    process = Process(
        argv=tuple(argv),
        input_digest=input_digest,
        description=f"Analyze PKL imports for {source_file}",
    )
    result = await Get(FallibleProcessResult, Process, process)

    # Choose parsing strategy based on process success.
    if result.exit_code == 0 and result.stdout:
        import_paths = _parse_analyze_output(result.stdout, source_file)
    else:
        # Fallback: regex over the source text.
        digest_contents = await Get(DigestContents, Digest, sources.snapshot.digest)
        source_text = ""
        for fc in digest_contents:
            if fc.path == source_file:
                source_text = fc.content.decode(errors="replace")
                break
        import_paths = _extract_local_paths_from_regex(source_text, source_file)

    if not import_paths:
        return InferredDependencies([])

    # Build a map from each generated pkl_source target's file path to its address.
    path_to_address: dict[str, Address] = {}
    for tgt in all_targets:
        if not tgt.has_field(PklSourceField):
            continue
        src_field = tgt[PklSourceField]
        if src_field.value:
            path_to_address[src_field.file_path] = tgt.address

    addresses: list[Address] = []
    for imp_path in import_paths:
        # Try exact match first.
        if imp_path in path_to_address:
            addresses.append(path_to_address[imp_path])
            continue
        # Try matching by suffix (handles absolute paths from analyze output).
        for known_path, addr in path_to_address.items():
            if imp_path.endswith(known_path) or known_path.endswith(imp_path):
                addresses.append(addr)
                break

    return InferredDependencies(addresses)


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferPklDependenciesRequest),
    ]
