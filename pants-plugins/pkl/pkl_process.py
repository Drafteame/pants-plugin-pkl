"""Shared helper for building pkl CLI argument vectors."""

from __future__ import annotations

from pants.engine.rules import collect_rules


def build_pkl_argv(
    exe: str,
    subcommand: str | tuple[str, ...],
    *args: str,
    project_dir: str | None = None,
    extra_args: tuple[str, ...] = (),
    include_common_flags: bool = True,
) -> list[str]:
    """Build a pkl command with standard sandbox containment flags.

    Args:
        exe: Path to the pkl binary.
        subcommand: The pkl subcommand — a single string like ``"eval"`` or a
            tuple like ``("analyze", "imports")`` for multi-word subcommands.
        *args: Positional arguments (e.g. source file paths).
        project_dir: Optional ``--project-dir`` value.
        extra_args: Extra CLI flags passed through from a subsystem or field.
        include_common_flags: If ``True`` (default), include ``--no-cache``,
            ``--color never``, ``--allowed-modules``, and
            ``--allowed-resources``.  Set to ``False`` for commands that do not
            accept these flags (e.g. ``pkl format``).

    Returns:
        A list of strings ready to pass as ``argv`` to a Pants ``Process``.

    Notes:
        * ``--root-dir .`` restricts file access to the sandbox root.
        * ``--no-cache`` prevents reading/writing ``~/.pkl/cache``.
        * ``--color never`` suppresses ANSI escape codes in captured output.
        * ``--allowed-modules`` removes ``https:`` and ``repl:`` (blocks
          network access and the REPL).
        * ``--allowed-resources`` keeps ``env:``, ``prop:``, and
          ``projectpackage:`` only (no ``file:``).
        * ``pkl format`` does NOT accept the common flags; use
          ``include_common_flags=False`` for that subcommand.
    """
    if isinstance(subcommand, str):
        subcommand = (subcommand,)

    argv: list[str] = [exe, *subcommand, "--root-dir", "."]

    if include_common_flags:
        argv.extend([
            "--no-cache",
            "--color", "never",
            "--allowed-modules", "pkl:,file:,modulepath:,projectpackage:",
            "--allowed-resources", "env:,prop:,projectpackage:",
        ])

    if project_dir:
        argv.extend(["--project-dir", project_dir])

    argv.extend(extra_args)
    argv.extend(args)
    return argv


def rules():
    return collect_rules()
