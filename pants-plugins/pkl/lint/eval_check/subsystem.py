"""Subsystem for the PKL eval-check lint rule."""

from pants.option.option_types import ArgsListOption, SkipOption
from pants.option.subsystem import Subsystem


class PklEvalCheck(Subsystem):
    options_scope = "pkl-eval-check"
    name = "pkl-eval-check"
    help = "Validates PKL source files by running `pkl eval` and checking for a zero exit code."

    skip = SkipOption("lint")
    args = ArgsListOption(example="--no-project")


def rules():
    from pants.engine.rules import collect_rules
    return collect_rules()
