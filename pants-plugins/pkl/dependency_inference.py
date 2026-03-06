"""Automatic dependency inference for PKL files.

Primary method: `pkl analyze imports -f json` — static analysis that extracts
all imports (including `import*`, `amends`, `extends`) without evaluating the
module.

Fallback method: regex over the source text — handles simple cases when the
primary method fails or for fast mode.

Both methods produce relative paths that are matched against known PKL source
targets (``pkl_source`` and ``pkl_test``) to produce ``Address`` values.

Dep inference is registered for both ``PklSourceField`` and
``PklTestSourceField`` so that test modules that import shared library modules
have their dependencies inferred correctly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pants.core.util_rules.external_tool import ExternalToolRequest, download_external_tool
from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import MergeDigests, PathGlobs
from pants.engine.intrinsics import execute_process, get_digest_contents, merge_digests, path_globs_to_digest
from pants.engine.platform import Platform
from pants.engine.process import Process
from pants.engine.rules import collect_rules, implicitly, rule
from pants.engine.target import (
    AllTargets,
    Dependencies,
    FieldSet,
    InferDependenciesRequest,
    InferredDependencies,
)
from pants.engine.unions import UnionRule

from pkl.pkl_process import PKL_PACKAGES_DIR, build_pkl_argv
from pkl.subsystem import PklTool
from pkl.target_types import PklProjectDirField, PklSourceField, PklTestSourceField

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
        # Normalize away ".." components using os.path.normpath-style logic.
        # PurePosixPath does NOT resolve ".." — we must do it manually.
        parts = resolved.split("/")
        normalized: list[str] = []
        for part in parts:
            if part == "..":
                if normalized:
                    normalized.pop()
            elif part and part != ".":
                normalized.append(part)
        resolved = "/".join(normalized)
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
    # We require the match to fall on a path-segment boundary so that, e.g.,
    # "deep/src/config.pkl" is not matched when we are looking for "src/config.pkl".
    source_stem = source_file.lstrip("/")
    source_deps: list[dict] = []
    for uri, deps in imports_map.items():
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            continue
        abs_path = parsed.path.lstrip("/")
        # Require either an exact match or that the suffix starts on a "/" boundary.
        if abs_path == source_stem or abs_path.endswith("/" + source_stem):
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
# Field sets & requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PklInferenceFieldSet(FieldSet):
    """FieldSet for ``pkl_source`` targets."""

    required_fields = (PklSourceField,)

    source: PklSourceField
    dependencies: Dependencies
    project_dir: PklProjectDirField


class InferPklDependenciesRequest(InferDependenciesRequest):
    infer_from = PklInferenceFieldSet


@dataclass(frozen=True)
class PklTestInferenceFieldSet(FieldSet):
    """FieldSet for ``pkl_test`` targets.

    Test modules may import shared source modules (e.g. ``import "lib.pkl"``).
    Registering a separate inference request for ``PklTestSourceField`` ensures
    those transitive dependencies are discovered automatically by Pants.
    """

    required_fields = (PklTestSourceField,)

    source: PklTestSourceField
    dependencies: Dependencies
    project_dir: PklProjectDirField


class InferPklTestDependenciesRequest(InferDependenciesRequest):
    infer_from = PklTestInferenceFieldSet


# ---------------------------------------------------------------------------
# Shared pure-Python helper (no Get calls — safe to call from any @rule)
# ---------------------------------------------------------------------------


def _resolve_import_addresses(
    import_paths: list[str],
    all_targets: AllTargets,
) -> list[Address]:
    """Map a list of import paths (from analyze or regex) to Pants Addresses.

    Builds a lookup table covering both ``pkl_source`` and ``pkl_test`` targets so
    that cross-type imports (e.g. a test file importing a source module) resolve
    correctly.

    Path matching requires segment boundaries to avoid false positives —
    ``"baz/src/config.pkl"`` will NOT match a lookup for ``"src/config.pkl"``.

    Args:
        import_paths: Sandbox-relative (or absolute temp-dir) paths returned by
            ``_parse_analyze_output`` or ``_extract_local_paths_from_regex``.
        all_targets: All known Pants targets.

    Returns:
        List of resolved ``Address`` values (deduplicated by order of discovery).
    """
    path_to_address: dict[str, Address] = {}
    for tgt in all_targets:
        for field_type in (PklSourceField, PklTestSourceField):
            if tgt.has_field(field_type):
                field = tgt[field_type]
                if field.value:
                    path_to_address[field.file_path] = tgt.address
                break

    addresses: list[Address] = []
    for imp_path in import_paths:
        # Try exact match first.
        if imp_path in path_to_address:
            addresses.append(path_to_address[imp_path])
            continue
        # Try suffix match on path-segment boundaries (handles absolute paths
        # returned by `pkl analyze imports` on macOS/Linux where the sandbox
        # root is an absolute temp directory).
        for known_path, addr in path_to_address.items():
            if imp_path == known_path:
                addresses.append(addr)
                break
            if imp_path.endswith("/" + known_path):
                addresses.append(addr)
                break
            if known_path.endswith("/" + imp_path):
                addresses.append(addr)
                break

    return addresses


# ---------------------------------------------------------------------------
# Inference rules
# ---------------------------------------------------------------------------


@rule(desc="Infer PKL source dependencies via pkl analyze imports")
async def infer_pkl_dependencies(
    request: InferPklDependenciesRequest,
    pkl: PklTool,
    platform: Platform,
    all_targets: AllTargets,
) -> InferredDependencies:
    field_set = request.field_set

    # Download the pkl binary.
    downloaded_pkl = await download_external_tool(pkl.get_request(platform))

    # Get the source file.
    sources = await determine_source_files(SourceFilesRequest([field_set.source]))
    if not sources.snapshot.files:
        return InferredDependencies([])

    source_file = sources.snapshot.files[0]

    # Include ALL PklProject, PklProject.deps.json, and vendored PKL packages so
    # `pkl analyze imports` can resolve both local and remote package:// deps.
    all_pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json", f"{PKL_PACKAGES_DIR}/**"])
    )

    # Merge binary + source + PklProject files into sandbox.
    input_digest = await merge_digests(
        MergeDigests((downloaded_pkl.digest, sources.snapshot.digest, all_pkl_project_digest))
    )

    # Run `pkl analyze imports -f json <source>`.
    argv = build_pkl_argv(
        downloaded_pkl.exe,
        ("analyze", "imports"),
        "-f", "json",
        source_file,
        project_dir=field_set.project_dir.value,
        use_cache=True,
    )

    result = await execute_process(
        **implicitly(
            Process(
                argv=tuple(argv),
                input_digest=input_digest,
                description=f"Analyze PKL imports for {source_file}",
            )
        )
    )

    # Choose parsing strategy based on process success.
    if result.exit_code == 0 and result.stdout:
        import_paths = _parse_analyze_output(result.stdout, source_file)
    else:
        # Fallback: regex over the source text.
        digest_contents = await get_digest_contents(sources.snapshot.digest)
        source_text = ""
        for fc in digest_contents:
            if fc.path == source_file:
                source_text = fc.content.decode(errors="replace")
                break
        import_paths = _extract_local_paths_from_regex(source_text, source_file)

    if not import_paths:
        return InferredDependencies([])

    return InferredDependencies(_resolve_import_addresses(import_paths, all_targets))


@rule(desc="Infer PKL test dependencies via pkl analyze imports")
async def infer_pkl_test_dependencies(
    request: InferPklTestDependenciesRequest,
    pkl: PklTool,
    platform: Platform,
    all_targets: AllTargets,
) -> InferredDependencies:
    field_set = request.field_set

    # Download the pkl binary.
    downloaded_pkl = await download_external_tool(pkl.get_request(platform))

    # Get the source file.
    sources = await determine_source_files(SourceFilesRequest([field_set.source]))
    if not sources.snapshot.files:
        return InferredDependencies([])

    source_file = sources.snapshot.files[0]

    # Include ALL PklProject, PklProject.deps.json, and vendored PKL packages so
    # `pkl analyze imports` can resolve both local and remote package:// deps.
    all_pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json", f"{PKL_PACKAGES_DIR}/**"])
    )

    # Merge binary + source + PklProject files into sandbox.
    input_digest = await merge_digests(
        MergeDigests((downloaded_pkl.digest, sources.snapshot.digest, all_pkl_project_digest))
    )

    # Run `pkl analyze imports -f json <source>`.
    argv = build_pkl_argv(
        downloaded_pkl.exe,
        ("analyze", "imports"),
        "-f", "json",
        source_file,
        project_dir=field_set.project_dir.value,
        use_cache=True,
    )

    result = await execute_process(
        **implicitly(
            Process(
                argv=tuple(argv),
                input_digest=input_digest,
                description=f"Analyze PKL imports for {source_file}",
            )
        )
    )

    # Choose parsing strategy based on process success.
    if result.exit_code == 0 and result.stdout:
        import_paths = _parse_analyze_output(result.stdout, source_file)
    else:
        # Fallback: regex over the source text.
        digest_contents = await get_digest_contents(sources.snapshot.digest)
        source_text = ""
        for fc in digest_contents:
            if fc.path == source_file:
                source_text = fc.content.decode(errors="replace")
                break
        import_paths = _extract_local_paths_from_regex(source_text, source_file)

    if not import_paths:
        return InferredDependencies([])

    return InferredDependencies(_resolve_import_addresses(import_paths, all_targets))


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferPklDependenciesRequest),
        UnionRule(InferDependenciesRequest, InferPklTestDependenciesRequest),
    ]
