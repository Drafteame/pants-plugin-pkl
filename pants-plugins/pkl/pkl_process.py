"""Shared helper for building pkl CLI argument vectors."""

from __future__ import annotations

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
        include_common_flags: If ``True`` (default), include ``--color never``,
            ``--allowed-modules``, and ``--allowed-resources``.  Set to
            ``False`` for commands that do not accept these flags (e.g.
            ``pkl format``).
        use_cache: If ``True``, enable the PKL package cache (needed for
            external ``package://`` dependencies).  When enabled, PKL is
            pointed at ``PKL_PACKAGES_DIR`` and ``https:`` is added to
            ``--allowed-resources`` so the cache download protocol is
            permitted.  When ``False`` (default), ``--no-cache`` is passed
            and ``https:`` is excluded.

    Returns:
        A list of strings ready to pass as ``argv`` to a Pants ``Process``.

    Notes:
        * ``--root-dir .`` restricts file access to the sandbox root.
        * ``--no-cache`` prevents reading/writing ``~/.pkl/cache``.
        * ``--color never`` suppresses ANSI escape codes in captured output.
        * ``--allowed-modules`` removes ``https:`` (blocks network access)
          but keeps ``repl:`` (required for ``pkl eval -x`` expressions).
        * ``--allowed-resources`` keeps ``env:``, ``prop:``, and
          ``projectpackage:`` only (no ``file:``).  When ``use_cache=True``,
          ``https:`` is also permitted so the PKL runtime can validate
          package checksums against the cache.
        * ``pkl format`` does NOT accept the common flags; use
          ``include_common_flags=False`` for that subcommand.
    """
    if isinstance(subcommand, str):
        subcommand = (subcommand,)

    argv: list[str] = [exe, *subcommand, "--root-dir", "."]

    if include_common_flags:
        if use_cache:
            argv.extend([
                "--cache-dir", PKL_PACKAGES_DIR,
                "--color", "never",
                "--allowed-modules", "pkl:,file:,modulepath:,projectpackage:,repl:",
                "--allowed-resources", "env:,prop:,projectpackage:,https:",
            ])
        else:
            argv.extend([
                "--no-cache",
                "--color", "never",
                "--allowed-modules", "pkl:,file:,modulepath:,projectpackage:,repl:",
                "--allowed-resources", "env:,prop:,projectpackage:",
            ])

    if project_dir:
        argv.extend(["--project-dir", project_dir])

    argv.extend(extra_args)
    argv.extend(args)
    return argv


def rules():
    return collect_rules()
