"""Shared helper for building pkl CLI argument vectors."""

from __future__ import annotations

from pathlib import PurePosixPath

from pants.engine.rules import collect_rules

# Path inside the sandbox (and in the repo) where vendored PKL packages live.
# This directory mirrors the PKL cache format (package-2/<host>/...) so that
# `--cache-dir PKL_PACKAGES_DIR` works without any network access.
PKL_PACKAGES_DIR = "pkl-packages"


def build_pkl_argv(
    exe: str,
    subcommand: str | tuple[str, ...],
    *args: str,
    project_dir: str | None = None,
    extra_args: tuple[str, ...] = (),
    include_common_flags: bool = True,
    use_cache: bool = False,
) -> list[str]:
    """Build a pkl command with standard sandbox containment flags.

    Args:
        exe: Path to the pkl binary.
        subcommand: The pkl subcommand — a single string like ``"eval"`` or a
            tuple like ``("analyze", "imports")`` for multi-word subcommands.
        *args: Positional arguments (e.g. source file paths).
        project_dir: Optional ``--project-dir`` value.
        extra_args: Extra CLI flags passed through from a subsystem or field.
        include_common_flags: If ``True`` (default), include ``--color never``
            and ``--cache-dir``/``--no-cache``.  Set to ``False`` for commands
            that do not accept these flags (e.g. ``pkl format``).
        use_cache: If ``True``, enable the PKL package cache (needed for
            external ``package://`` dependencies).  When enabled, PKL is
            pointed at ``PKL_PACKAGES_DIR``.  When ``False`` (default),
            ``--no-cache`` is passed to prevent reading/writing
            ``~/.pkl/cache``.

    Returns:
        A list of strings ready to pass as ``argv`` to a Pants ``Process``.

    Notes:
        * ``--root-dir .`` restricts file access to the sandbox root.
        * ``--no-cache`` prevents reading/writing ``~/.pkl/cache``.
        * ``--color never`` suppresses ANSI escape codes in captured output.
        * ``--allowed-modules '.*'`` allows any module URI scheme so that PKL
          packages can register custom schemes (e.g. ``formae:``, ``aws:``).
          PKL uses Java regex patterns; ``.*`` matches all URIs.
          The Pants sandbox already provides file-system isolation via
          ``--root-dir .``, so no additional module-level allowlisting is needed.
        * ``pkl format`` does NOT accept the common flags; use
          ``include_common_flags=False`` for that subcommand.
    """
    if isinstance(subcommand, str):
        subcommand = (subcommand,)

    argv: list[str] = [exe, *subcommand, "--root-dir", "."]

    if include_common_flags:
        # Allow any module URI scheme so that PKL packages can register custom
        # schemes (e.g. formae:, aws:).  File-system isolation is already
        # provided by --root-dir .; a module-level allowlist would only break
        # repos that depend on such custom schemes.
        # Note: PKL uses Java regex patterns, not glob; ".*" matches everything.
        argv.extend(["--allowed-modules", ".*"])
        if use_cache:
            argv.extend([
                "--cache-dir", PKL_PACKAGES_DIR,
                "--color", "never",
            ])
        else:
            argv.extend([
                "--no-cache",
                "--color", "never",
            ])

    if project_dir:
        argv.extend(["--project-dir", project_dir])

    argv.extend(extra_args)
    argv.extend(args)
    return argv


def detect_project_dir(
    source_file: str,
    sandbox_files: frozenset[str],
) -> str | None:
    """Walk up from the source file's directory to find the nearest PklProject.

    PKL's own project discovery walks up from the *source file* directory,
    not from the CWD.  Inside a Pants sandbox the CWD is the sandbox root
    so PKL never finds PklProject files that live next to (or above) the
    source unless ``--project-dir`` is given explicitly.

    This function replicates that walk using the sandbox file set so that
    every rule can derive an effective ``project_dir`` automatically when
    the user hasn't specified one in the BUILD file.

    Args:
        source_file: Sandbox-relative path to the PKL source file.
        sandbox_files: Frozenset of all sandbox-relative file paths
            (e.g. ``frozenset(snapshot.files)``).  Must include
            ``**/PklProject`` entries for detection to work.

    Returns:
        The sandbox-relative directory containing the nearest ``PklProject``,
        or ``None`` if no PklProject is found in any ancestor.
    """
    current = PurePosixPath(source_file).parent

    while True:
        candidate = str(current / "PklProject")
        if candidate in sandbox_files:
            dir_str = str(current)
            return dir_str if dir_str != "." else None
        if current == PurePosixPath(".") or current == current.parent:
            break
        current = current.parent

    return None


def rules():
    return collect_rules()
