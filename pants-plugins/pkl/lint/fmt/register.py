"""Backend registration for `pkl.lint.fmt`."""

from pkl.lint.fmt import rules as fmt_rules
from pkl.lint.fmt import subsystem as fmt_subsystem


def rules():
    return [
        *fmt_subsystem.rules(),
        *fmt_rules.rules(),
    ]
