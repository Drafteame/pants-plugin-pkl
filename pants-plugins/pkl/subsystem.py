"""PKL tool subsystem — manages downloading and running the pkl binary.

Provides two binary resolution strategies controlled by ``[pkl].use_system_binary``:

1. **System-first (default):** Search ``$PATH`` for a ``pkl`` binary. If found
   and the version meets ``[pkl].minimum_version``, use it.  If not found or
   the version is too old, fall back to downloading.
2. **Download-only:** Always download the version specified by ``[pkl].version``
   (set ``use_system_binary = false``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pants.core.util_rules.external_tool import ExternalTool, download_external_tool
from pants.core.util_rules.system_binaries import (
    BinaryPathRequest,
    BinaryPathTest,
    BinaryPaths,
    find_binary,
)
from pants.engine.fs import EMPTY_DIGEST, Digest
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules, implicitly, rule
from pants.option.option_types import BoolOption, StrListOption, StrOption

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PklBinary — the resolved binary, consumed by all other rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PklBinary:
    """A resolved pkl binary, either from the system or downloaded."""

    exe: str
    digest: Digest
    version: str
    is_system: bool  # True if from $PATH, False if downloaded


@dataclass(frozen=True)
class PklBinaryRequest:
    """Singleton request to resolve the pkl binary."""

    pass


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

# Matches "Pkl 0.28.0 ..." or "Pkl 0.32.0-dev ..." — captures only the
# numeric major.minor.patch portion.
_PKL_VERSION_RE = re.compile(r"Pkl\s+(\d+\.\d+\.\d+)")


def _parse_pkl_version(version_output: str) -> str | None:
    """Extract version string from ``pkl --version`` output.

    Returns ``"0.28.0"`` from ``"Pkl 0.28.0 (Linux, Native)"``,
    or ``None`` if parsing fails.
    """
    m = _PKL_VERSION_RE.search(version_output)
    return m.group(1) if m else None


def _version_tuple(version_str: str) -> tuple[int, ...]:
    """Convert ``"0.28.0"`` to ``(0, 28, 0)`` for comparison."""
    return tuple(int(x) for x in version_str.split("."))


def _version_gte(version: str, minimum: str) -> bool:
    """Return ``True`` if *version* >= *minimum* (semver tuple comparison)."""
    return _version_tuple(version) >= _version_tuple(minimum)


# ---------------------------------------------------------------------------
# PklTool subsystem (ExternalTool for download path, plus new options)
# ---------------------------------------------------------------------------


class PklTool(ExternalTool):
    """The PKL configuration language CLI (https://pkl-lang.org)."""

    options_scope = "pkl"
    name = "pkl"
    help = "The PKL configuration language CLI (https://pkl-lang.org)"

    default_version = "0.31.0"
    default_known_versions = [
        "0.31.0|macos_arm64|349402ae32c35382c034b0c0af744ffb0d53a213888c44deec94a7810e144889|98193008",
        "0.31.0|macos_x86_64|9f1cc8e3ac2327bc483b90d0c220da20eb785c3ba3fe92e021f47d3d56768282|100326344",
        "0.31.0|linux_x86_64|5a5c2a889b68ca92ff4258f9d277f92412b98dfef5057daef7564202a20870b6|100535568",
        "0.31.0|linux_arm64|471460cdd11e1cb9ac0a5401fdb05277ae3adb3a4573cc0a9c63ee087c1f93c8|97586680",
    ]

    platform_mapping = {
        "macos_arm64": "macos-aarch64",
        "macos_x86_64": "macos-amd64",
        "linux_x86_64": "linux-amd64",
        "linux_arm64": "linux-aarch64",
    }

    # --- New options ---

    use_system_binary = BoolOption(
        default=True,
        help=(
            "If true (default), look for a `pkl` binary on the system PATH "
            "before downloading. If a suitable system binary is found "
            "(meeting minimum_version), it will be used. If not found, "
            "the plugin downloads the version specified by --version.\n\n"
            "Set to false to always download the binary (fully hermetic builds)."
        ),
    )

    minimum_version = StrOption(
        default="0.27.0",
        help=(
            "Minimum acceptable pkl version when using a system binary. "
            "The core plugin requires >= 0.27.0 (for `pkl analyze imports` "
            "and `--color never`). The `pkl.lint.fmt` backend requires "
            ">= 0.30.0.\n\n"
            "If the system binary is below this version, the plugin falls "
            "back to downloading."
        ),
        advanced=True,
    )

    search_path = StrListOption(
        default=["<PATH>"],
        help=(
            "Directories to search for the `pkl` binary. The special string "
            "'<PATH>' expands to the contents of the PATH environment variable."
        ),
        advanced=True,
    )

    def generate_url(self, plat: Platform) -> str:
        plat_str = self.platform_mapping[plat.value]
        return (
            f"https://github.com/apple/pkl/releases/download/"
            f"{self.version}/pkl-{plat_str}"
        )

    def generate_exe(self, plat: Platform) -> str:
        plat_str = self.platform_mapping[plat.value]
        return f"./pkl-{plat_str}"


# ---------------------------------------------------------------------------
# Binary resolution rule
# ---------------------------------------------------------------------------


@rule(desc="Resolve pkl binary")
async def resolve_pkl_binary(
    request: PklBinaryRequest,
    pkl_tool: PklTool,
    platform: Platform,
) -> PklBinary:
    # --- Strategy 1: Try system binary ---
    if pkl_tool.use_system_binary:
        binary_paths: BinaryPaths = await find_binary(
            BinaryPathRequest(
                binary_name="pkl",
                search_path=pkl_tool.search_path,
                # fingerprint_stdout=False gives us the raw stdout string
                # (e.g. "Pkl 0.28.0 (Linux, Native)\n") instead of a SHA-256
                # hash, so we can parse the version out of it.
                test=BinaryPathTest(
                    args=["--version"],
                    fingerprint_stdout=False,
                ),
            ),
            **implicitly(),
        )

        if binary_paths.first_path is not None:
            path_entry = binary_paths.first_path
            exe_path = path_entry.path
            version_output = path_entry.fingerprint  # raw stdout

            version = _parse_pkl_version(version_output)
            if version and _version_gte(version, pkl_tool.minimum_version):
                logger.info(
                    "Using system pkl binary at %s (version %s)",
                    exe_path,
                    version,
                )
                return PklBinary(
                    exe=exe_path,
                    digest=EMPTY_DIGEST,
                    version=version,
                    is_system=True,
                )
            elif version:
                logger.info(
                    "System pkl binary at %s is version %s, "
                    "below minimum %s. Falling back to download.",
                    exe_path,
                    version,
                    pkl_tool.minimum_version,
                )
            else:
                logger.info(
                    "Could not determine version of system pkl at %s. "
                    "Falling back to download.",
                    exe_path,
                )

    # --- Strategy 2: Download ---
    logger.info("Using downloaded pkl binary (version %s)", pkl_tool.version)
    downloaded = await download_external_tool(pkl_tool.get_request(platform))
    return PklBinary(
        exe=downloaded.exe,
        digest=downloaded.digest,
        version=pkl_tool.version,
        is_system=False,
    )


def rules():
    return collect_rules()
