"""Remote PKL package resolution.

Provides ``PklResolvedPackages`` — a Digest containing all external PKL
packages needed by the project, ready to be merged into any sandbox.

Resolution strategy (controlled by ``[pkl].package_resolve_mode``):

1. **auto** (default): Use vendored ``pkl-packages/`` if present, otherwise
   parse ``PklProject.deps.json`` and download via ``pkl download-package``.
2. **vendored**: Always use ``pkl-packages/``; error if missing.
3. **download**: Always download from ``PklProject.deps.json``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from pants.engine.fs import EMPTY_DIGEST, Digest, MergeDigests, PathGlobs
from pants.engine.intrinsics import (
    digest_to_snapshot,
    execute_process,
    get_digest_contents,
    merge_digests,
    path_globs_to_digest,
)
from pants.engine.process import Process, ProcessCacheScope
from pants.engine.rules import collect_rules, implicitly, rule

from pkl.pkl_process import PKL_PACKAGES_DIR
from pkl.subsystem import PklBinary, PklPackageResolveMode, PklTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PklPackageEntry:
    """A single resolved remote dependency from PklProject.deps.json."""

    canonical_uri: str     # e.g. "package://example.com/foo/bar@0"
    resolved_uri: str      # e.g. "projectpackage://example.com/foo/bar@0.5.0"
    metadata_sha256: str   # SHA-256 hex of the metadata JSON file
    host: str              # e.g. "example.com"
    path_prefix: str       # e.g. "foo/bar"
    name: str              # e.g. "bar"
    version: str           # e.g. "0.5.0"
    metadata_url: str      # e.g. "https://example.com/foo/bar@0.5.0"


@dataclass(frozen=True)
class PklResolvedPackagesRequest:
    """Singleton request to resolve all external PKL packages."""

    pass


@dataclass(frozen=True)
class PklResolvedPackages:
    """Digest containing all external packages in pkl-packages/ layout.

    Empty digest if no external packages are needed.
    """

    digest: Digest


# ---------------------------------------------------------------------------
# deps.json parser
# ---------------------------------------------------------------------------

# The only PklProject.deps.json schema version this plugin understands.
# If PKL introduces a new schema version, the parser will log a warning and
# skip that file rather than silently returning nothing.
_SUPPORTED_DEPS_SCHEMA_VERSION = 1


def _parse_deps_json(content: bytes) -> list[PklPackageEntry]:
    """Parse ``PklProject.deps.json`` and return remote dependency entries.

    Local dependencies (``type == "local"``) are skipped — Pants handles
    them via normal dependency inference on the filesystem.

    Returns an empty list on any parse error (does not raise).
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []

    schema = data.get("schemaVersion")
    if schema != _SUPPORTED_DEPS_SCHEMA_VERSION:
        if schema is not None:
            logger.warning(
                "PklProject.deps.json has schemaVersion %s (expected %s); "
                "skipping remote dependency resolution for this file. "
                "You may need to update the pkl Pants plugin.",
                schema,
                _SUPPORTED_DEPS_SCHEMA_VERSION,
            )
        return []

    entries: list[PklPackageEntry] = []
    resolved = data.get("resolvedDependencies", {})

    for canonical_uri, dep_info in resolved.items():
        if dep_info.get("type") != "remote":
            continue

        resolved_uri = dep_info.get("uri", "")
        sha256 = dep_info.get("checksums", {}).get("sha256", "")
        if not sha256 or not resolved_uri:
            continue

        # Parse the resolved URI to extract host, path, name, version.
        # resolved_uri: "projectpackage://pkg.pkl-lang.org/foo/bar@0.5.0"
        parsed = urlparse(resolved_uri.replace("projectpackage://", "https://"))
        host = parsed.hostname or ""
        raw_path = parsed.path.lstrip("/")  # "foo/bar@0.5.0"
        if "@" not in raw_path:
            continue
        path_part, version = raw_path.rsplit("@", 1)
        name = path_part.rsplit("/", 1)[-1] if "/" in path_part else path_part
        metadata_url = f"https://{host}/{path_part}@{version}"

        entries.append(
            PklPackageEntry(
                canonical_uri=canonical_uri,
                resolved_uri=resolved_uri,
                metadata_sha256=sha256,
                host=host,
                path_prefix=path_part,
                name=name,
                version=version,
                metadata_url=metadata_url,
            )
        )

    return entries


# ---------------------------------------------------------------------------
# Resolution rule
# ---------------------------------------------------------------------------


@rule(desc="Resolve external PKL packages")
async def resolve_pkl_packages(
    request: PklResolvedPackagesRequest,
    pkl_binary: PklBinary,
    pkl_tool: PklTool,
) -> PklResolvedPackages:
    mode = pkl_tool.package_resolve_mode

    # ----- Vendored path -----
    if mode in (PklPackageResolveMode.AUTO, PklPackageResolveMode.VENDORED):
        vendored_digest = await path_globs_to_digest(
            PathGlobs([f"{PKL_PACKAGES_DIR}/**"])
        )
        vendored_snapshot = await digest_to_snapshot(vendored_digest)

        if vendored_snapshot.files:
            logger.info(
                "Using vendored %s/ directory (%d files)",
                PKL_PACKAGES_DIR,
                len(vendored_snapshot.files),
            )
            return PklResolvedPackages(digest=vendored_digest)

        if mode == PklPackageResolveMode.VENDORED:
            raise FileNotFoundError(
                f"[pkl].package_resolve_mode is 'vendored' but no "
                f"{PKL_PACKAGES_DIR}/ directory was found. Run "
                f"`pkl project resolve -o {PKL_PACKAGES_DIR}/` and commit "
                f"the result, or switch to 'auto' or 'download' mode."
            )

    # ----- Download path -----
    # (Reached when mode is AUTO with no vendored dir, or mode is DOWNLOAD.)
    deps_json_digest = await path_globs_to_digest(
        PathGlobs(["**/PklProject.deps.json"])
    )
    deps_json_contents = await get_digest_contents(deps_json_digest)

    if not deps_json_contents:
        return PklResolvedPackages(digest=EMPTY_DIGEST)

    # Parse all deps.json files and collect unique remote entries.
    all_entries: dict[str, PklPackageEntry] = {}
    for fc in deps_json_contents:
        for entry in _parse_deps_json(fc.content):
            all_entries[entry.canonical_uri] = entry

    if not all_entries:
        return PklResolvedPackages(digest=EMPTY_DIGEST)

    # Build URIs with embedded checksums for `pkl download-package`.
    # Format: package://host/path@version::sha256:<hash>
    uris_with_checksums: list[str] = []
    for entry in all_entries.values():
        pkg_uri = entry.resolved_uri.replace("projectpackage://", "package://")
        uris_with_checksums.append(
            f"{pkg_uri}::sha256:{entry.metadata_sha256}"
        )

    logger.info(
        "Downloading %d external PKL package(s) via pkl download-package",
        len(uris_with_checksums),
    )

    # Run `pkl download-package --cache-dir pkl-packages <uri1> <uri2> ...`
    argv = [
        pkl_binary.exe,
        "download-package",
        "--cache-dir",
        PKL_PACKAGES_DIR,
        *uris_with_checksums,
    ]

    input_digest = pkl_binary.digest  # pkl binary (or EMPTY_DIGEST if system)

    result = await execute_process(
        **implicitly(
            Process(
                argv=tuple(argv),
                input_digest=input_digest,
                output_directories=(PKL_PACKAGES_DIR,),
                description=(
                    f"Download {len(uris_with_checksums)} PKL package(s)"
                ),
                # This process needs network access. PER_SESSION ensures
                # Pants caches the result within a build session but
                # re-executes if inputs (deps.json) change between sessions.
                cache_scope=ProcessCacheScope.PER_SESSION,
            )
        )
    )

    return PklResolvedPackages(digest=result.output_digest)


def rules():
    return collect_rules()
