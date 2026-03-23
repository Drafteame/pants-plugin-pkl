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

Alias import support (``@alias/...``):
    PKL supports project-scoped dependencies declared in a ``PklProject`` file.
    An import like ``import "@baseconfig/global.pkl"`` uses an alias defined in::

        dependencies {
          ["baseconfig"] = import("../../../../config/pkl/PklProject")
        }

    This module reads the nearest ``PklProject`` to build an *alias map*
    (``{alias: repo-relative-dep-dir}``) and uses it to resolve these imports
    to concrete source file paths.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pants.core.util_rules.source_files import SourceFilesRequest, determine_source_files
from pants.engine.addresses import Address
from pants.engine.fs import MergeDigests, PathGlobs
from pants.engine.intrinsics import digest_to_snapshot, execute_process, get_digest_contents, merge_digests, path_globs_to_digest
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

from pkl.pkl_dependencies import PklResolvedPackagesRequest, resolve_pkl_packages
from pkl.pkl_process import build_pkl_argv, detect_project_dir
from pkl.subsystem import PklBinaryRequest, resolve_pkl_binary
from pkl.target_types import PklProjectDirField, PklSourceField, PklTestSourceField

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

#: Matches PKL import-like statements: ``import``, ``import*``, ``amends``,
#: ``extends``, followed by a double-quoted string literal.
PKL_IMPORT_RE = re.compile(
    r'^\s*(?:import\*?|amends|extends)\s+"([^"]+)"',
    re.MULTILINE,
)

#: Matches local dependency declarations in PklProject files::
#:
#:     ["alias"] = import("../relative/path/PklProject")
#:
#: Allows optional whitespace inside the ``import(...)`` call.
_PKL_PROJECT_LOCAL_DEP_RE = re.compile(
    r'\["([^"]+)"\]\s*=\s*import\(\s*"([^"]+)"\s*\)',
    re.MULTILINE,
)

#: URI schemes that do NOT correspond to local files and should be ignored.
_IGNORED_SCHEMES = frozenset({"pkl", "package", "https", "http", "modulepath", "projectpackage"})


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Normalize a POSIX path by resolving ``..`` and ``.`` segments.

    ``PurePosixPath`` does not collapse ``..`` components; this function
    replicates the behaviour of ``os.path.normpath`` for POSIX paths without
    touching the filesystem.

    Args:
        path: A POSIX-style path, potentially containing ``..`` or ``.``.

    Returns:
        The normalized path with ``..`` and ``.`` resolved.
    """
    parts = path.split("/")
    result: list[str] = []
    for part in parts:
        if part == "..":
            if result:
                result.pop()
        elif part and part != ".":
            result.append(part)
    return "/".join(result)


# ---------------------------------------------------------------------------
# PKL project alias map
# ---------------------------------------------------------------------------


def _parse_pkl_project_local_deps(content: str, project_dir: str) -> dict[str, str]:
    """Parse a ``PklProject`` file and return ``{alias: repo-relative-dep-dir}``.

    Handles local dependency declarations of the form::

        dependencies {
          ["baseconfig"] = import("../../../../config/pkl/PklProject")
        }

    The returned value maps each alias to the normalised repo-relative
    directory that contains the dependency's own ``PklProject`` file.
    For the example above (with ``project_dir="services/app/config"``):

        ``{"baseconfig": "config/pkl"}``

    Remote dependencies (using ``{ uri = "package://..." }`` syntax) are
    silently ignored — Pants resolves them via :mod:`pkl.pkl_dependencies`.

    Args:
        content: Raw text content of the ``PklProject`` file.
        project_dir: Repo-relative directory containing this ``PklProject``
            (used to resolve the relative import paths inside the file).

    Returns:
        Mapping from alias name to the normalized repo-relative directory of
        the local dependency.
    """
    result: dict[str, str] = {}
    for m in _PKL_PROJECT_LOCAL_DEP_RE.finditer(content):
        alias = m.group(1)
        import_path = m.group(2)  # e.g. "../../../../config/pkl/PklProject"

        # Resolve the import path relative to the project_dir, then take the
        # parent directory (stripping the "PklProject" filename component).
        if project_dir:
            combined = f"{project_dir}/{import_path}"
        else:
            combined = import_path

        dep_dir = _normalize_path(str(PurePosixPath(combined).parent))
        result[alias] = dep_dir

    return result


def _resolve_projectpackage_uri(uri: str, alias_map: dict[str, str]) -> str | None:
    """Resolve a ``projectpackage://`` URI to a repo-relative file path.

    ``pkl analyze imports -f json`` emits ``projectpackage://`` URIs for
    imports that reference a project dependency alias, e.g.::

        projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl

    Given an alias map derived from the source file's ``PklProject``::

        {"baseconfig": "config/pkl"}

    …this function returns ``"config/pkl/global.pkl"``.

    Args:
        uri: A ``projectpackage://`` URI from ``pkl analyze imports`` output.
        alias_map: Mapping from package name → repo-relative source directory.

    Returns:
        Normalised repo-relative path, or ``None`` if the URI cannot be
        resolved via the alias map.
    """
    if not uri.startswith("projectpackage://"):
        return None
    if "#" not in uri:
        return None

    base, fragment = uri.split("#", 1)
    file_path = fragment.lstrip("/")  # "global.pkl"
    if not file_path:
        return None

    # base = "projectpackage://localhost:0/baseconfig@1.0.0"
    # Strip the scheme prefix to isolate "localhost:0/baseconfig@1.0.0".
    after_scheme = base[len("projectpackage://"):]
    if "/" not in after_scheme:
        return None

    # Drop the host:port component; pkg_path = "baseconfig@1.0.0"
    # (or "namespace/subname@1.0.0" for namespaced packages).
    _, pkg_path = after_scheme.split("/", 1)
    # Take the last path segment and strip the version suffix.
    pkg_name = pkg_path.split("@")[0].rsplit("/", 1)[-1]

    if pkg_name not in alias_map:
        return None

    return _normalize_path(f"{alias_map[pkg_name]}/{file_path}")


# ---------------------------------------------------------------------------
# Regex fallback parser
# ---------------------------------------------------------------------------


def _extract_local_paths_from_regex(
    source_text: str,
    source_file: str,
    alias_map: dict[str, str] | None = None,
) -> list[str]:
    """Return sandbox-relative paths implied by the source text using the regex fallback.

    Args:
        source_text: Raw PKL source text.
        source_file: Sandbox-relative path of the source file being analysed
            (used to resolve relative imports).
        alias_map: Optional mapping from project alias to repo-relative dep
            directory.  Required to resolve ``@alias/...`` imports; when
            ``None`` those imports are silently skipped.

    Returns:
        List of sandbox-relative paths for *local* imports only.
    """
    source_dir = str(PurePosixPath(source_file).parent)
    paths: list[str] = []
    for m in PKL_IMPORT_RE.finditer(source_text):
        uri = m.group(1)

        # ------------------------------------------------------------------
        # @alias/... imports (PKL project dependency aliases)
        # ------------------------------------------------------------------
        if uri.startswith("@"):
            if alias_map:
                slash_pos = uri.find("/", 1)
                if slash_pos != -1:
                    alias = uri[1:slash_pos]
                    file_path = uri[slash_pos + 1:]
                    if alias in alias_map and file_path:
                        paths.append(_normalize_path(f"{alias_map[alias]}/{file_path}"))
            # @alias/... is never a repo-relative path — skip regardless of
            # whether we resolved it.
            continue

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
        resolved = _normalize_path(resolved)
        paths.append(resolved)
    return paths


# ---------------------------------------------------------------------------
# JSON output parsing
# ---------------------------------------------------------------------------


def _parse_analyze_output(
    json_bytes: bytes,
    source_file: str,
    alias_map: dict[str, str] | None = None,
) -> list[str]:
    """Parse ``pkl analyze imports -f json`` output into sandbox-relative import paths.

    The JSON format is::

        {
          "imports": {
            "file:///abs/path/to/source.pkl": [
              {"uri": "file:///abs/path/to/dep.pkl"},
              {"uri": "projectpackage://localhost:0/baseconfig@1.0.0#/global.pkl"}
            ]
          }
        }

    ``file://`` entries are converted to relative paths as before.
    ``projectpackage://`` entries are resolved via *alias_map* when provided.

    We locate the entry whose key ends with ``source_file`` and return the
    relative paths of its direct imports.

    Args:
        json_bytes: Raw stdout from ``pkl analyze imports -f json``.
        source_file: Sandbox-relative path of the file whose deps we want.
        alias_map: Optional mapping from package alias to repo-relative source
            directory, used to resolve ``projectpackage://`` import URIs.

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
        if parsed.scheme == "file":
            abs_path = parsed.path  # /private/var/folders/.../subdir/dep.pkl
            # Keep only the portion starting from source_stem's directory or just
            # use the last N components.  We look for the longest suffix that ends
            # in a .pkl file and try to match against known targets below.
            paths.append(abs_path.lstrip("/"))
        elif parsed.scheme == "projectpackage" and alias_map:
            # Local project dependency alias — resolve via alias map.
            resolved = _resolve_projectpackage_uri(dep_uri, alias_map)
            if resolved:
                paths.append(resolved)

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
# Shared async inference helper
# ---------------------------------------------------------------------------


async def _run_pkl_inference(
    source_field,
    project_dir_field,
    all_targets: AllTargets,
) -> InferredDependencies:
    """Core inference logic shared by source and test rules.

    Runs ``pkl analyze imports -f json``, falling back to regex if the command
    fails.  Both strategies now honour ``@alias/...`` imports via the
    ``PklProject`` alias map.
    """
    # Resolve the pkl binary (system or downloaded).
    pkl_binary = await resolve_pkl_binary(PklBinaryRequest())

    # Get the source file.
    sources = await determine_source_files(SourceFilesRequest([source_field]))
    if not sources.snapshot.files:
        return InferredDependencies([])

    source_file = sources.snapshot.files[0]

    # Include PklProject, PklProject.deps.json, and resolved PKL packages so
    # `pkl analyze imports` can resolve both local and remote package:// deps.
    pkl_project_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject", "**/PklProject.deps.json"])
    )
    resolved_packages = await resolve_pkl_packages(PklResolvedPackagesRequest())
    all_pkl_project_digest = await merge_digests(
        MergeDigests((pkl_project_digest, resolved_packages.digest))
    )

    # Merge binary + source + PklProject files into sandbox.
    input_digest = await merge_digests(
        MergeDigests((pkl_binary.digest, sources.snapshot.digest, all_pkl_project_digest))
    )

    # Auto-detect project_dir if not explicitly set.
    sandbox_snapshot = await digest_to_snapshot(input_digest)
    effective_project_dir = project_dir_field.value or detect_project_dir(
        source_file, frozenset(sandbox_snapshot.files)
    )

    # Build the alias map from the nearest PklProject so that @alias/... imports
    # can be resolved to repo-relative file paths.
    alias_map: dict[str, str] = {}
    if effective_project_dir:
        pkl_project_contents = await get_digest_contents(pkl_project_digest)
        project_file_path = f"{effective_project_dir}/PklProject"
        for fc in pkl_project_contents:
            if fc.path == project_file_path:
                content = fc.content.decode(errors="replace")
                alias_map = _parse_pkl_project_local_deps(content, effective_project_dir)
                break

    # Run `pkl analyze imports -f json <source>`.
    argv = build_pkl_argv(
        pkl_binary.exe,
        ("analyze", "imports"),
        "-f", "json",
        source_file,
        project_dir=effective_project_dir,
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
        import_paths = _parse_analyze_output(result.stdout, source_file, alias_map=alias_map)
    else:
        # Fallback: regex over the source text.
        digest_contents = await get_digest_contents(sources.snapshot.digest)
        source_text = ""
        for fc in digest_contents:
            if fc.path == source_file:
                source_text = fc.content.decode(errors="replace")
                break
        import_paths = _extract_local_paths_from_regex(source_text, source_file, alias_map=alias_map)

    if not import_paths:
        return InferredDependencies([])

    return InferredDependencies(_resolve_import_addresses(import_paths, all_targets))


# ---------------------------------------------------------------------------
# Inference rules
# ---------------------------------------------------------------------------


@rule(desc="Infer PKL source dependencies via pkl analyze imports")
async def infer_pkl_dependencies(
    request: InferPklDependenciesRequest,
    all_targets: AllTargets,
) -> InferredDependencies:
    return await _run_pkl_inference(
        request.field_set.source,
        request.field_set.project_dir,
        all_targets,
    )


@rule(desc="Infer PKL test dependencies via pkl analyze imports")
async def infer_pkl_test_dependencies(
    request: InferPklTestDependenciesRequest,
    all_targets: AllTargets,
) -> InferredDependencies:
    return await _run_pkl_inference(
        request.field_set.source,
        request.field_set.project_dir,
        all_targets,
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(InferDependenciesRequest, InferPklDependenciesRequest),
        UnionRule(InferDependenciesRequest, InferPklTestDependenciesRequest),
    ]
