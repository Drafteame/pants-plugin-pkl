"""Backend registration for `pkl.lint.eval_check`."""

from pkl.lint.eval_check import rules as eval_check_rules
from pkl.lint.eval_check import subsystem as eval_check_subsystem


def rules():
    return [
        *eval_check_subsystem.rules(),
        *eval_check_rules.rules(),
    ]
