"""Auto-generate BUILD targets for `.pkl` files (`pants tailor`).

Detection logic:
- Read the first few lines of each `.pkl` file.
- Files containing ``amends "pkl:test"`` -> grouped as ``pkl_tests()``.
- Files named ``PklProject`` -> excluded entirely.
- All other `.pkl` files -> grouped as ``pkl_sources()``.

One ``pkl_sources()`` and/or ``pkl_tests()`` target is created per directory.
"""

from __future__ import annotations

from dataclasses import dataclass

from pants.core.goals.tailor import (
    AllOwnedSources,
    PutativeTarget,
    PutativeTargets,
    PutativeTargetsRequest,
)
from pants.engine.fs import PathGlobs
from pants.engine.intrinsics import (
    digest_to_snapshot,
    get_digest_contents,
    path_globs_to_digest,
    path_globs_to_paths,
)
from pants.engine.rules import collect_rules, rule
from pants.engine.unions import UnionRule
from pants.util.dirutil import group_by_dir

from pkl.target_types import PklSourcesTarget, PklTestsTarget

# Marker that identifies a PKL test module.
_PKL_TEST_MARKER = b'amends "pkl:test"'

# Files with this exact basename are PKL project descriptors and should NOT
# be included in any generated target.
_PKL_PROJECT_BASENAME = "PklProject"

# How many bytes of each file to read when looking for the test marker.
_HEADER_BYTES = 512


@dataclass(frozen=True)
class PutativePklTargetsRequest(PutativeTargetsRequest):
    pass


@rule(desc="Determine candidate PKL targets to create")
async def find_putative_pkl_targets(
    request: PutativePklTargetsRequest,
    all_owned_sources: AllOwnedSources,
) -> PutativeTargets:
    # 1. Glob all .pkl files in the requested directories.
    all_pkl_paths = await path_globs_to_paths(request.path_globs("*.pkl"))

    # 2. Filter out already-owned files.
    unowned = set(all_pkl_paths.files) - set(all_owned_sources)

    # 3. Exclude PklProject files.
    unowned = {
        p for p in unowned
        if not p.endswith(f"/{_PKL_PROJECT_BASENAME}")
        and p != _PKL_PROJECT_BASENAME
    }

    if not unowned:
        return PutativeTargets([])

    # 4. Read file headers to detect test files (look for `amends "pkl:test"`).
    snapshot_digest = await path_globs_to_digest(PathGlobs(list(unowned)))
    snapshot = await digest_to_snapshot(snapshot_digest)
    digest_contents = await get_digest_contents(snapshot.digest)

    content_map: dict[str, bytes] = {
        fc.path: fc.content[:_HEADER_BYTES] for fc in digest_contents
    }

    # 5. Classify files into test vs source.
    test_files: set[str] = set()
    source_files: set[str] = set()

    for path in unowned:
        header = content_map.get(path, b"")
        if _PKL_TEST_MARKER in header:
            test_files.add(path)
        else:
            source_files.add(path)

    # 6. Group by directory and create PutativeTargets.
    putative: list[PutativeTarget] = []

    for dirname, filenames in sorted(group_by_dir(source_files).items()):
        putative.append(
            PutativeTarget.for_target_type(
                PklSourcesTarget,
                path=dirname,
                name=None,  # defaults to basename of dir
                triggering_sources=sorted(filenames),
            )
        )

    for dirname, filenames in sorted(group_by_dir(test_files).items()):
        putative.append(
            PutativeTarget.for_target_type(
                PklTestsTarget,
                path=dirname,
                name="tests",
                triggering_sources=sorted(filenames),
            )
        )

    return PutativeTargets(putative)


def rules():
    return [
        *collect_rules(),
        UnionRule(PutativeTargetsRequest, PutativePklTargetsRequest),
    ]
