"""Subsystem for the PKL formatter (`pkl format`)."""

from pants.option.option_types import ArgsListOption, SkipOption
from pants.option.subsystem import Subsystem


class PklFmt(Subsystem):
    options_scope = "pkl-fmt"
    name = "pkl-fmt"
    help = (
        "Formats PKL source files using `pkl format`. "
        "Requires PKL >= 0.30.0."
    )

    skip = SkipOption("fmt", "lint")
    args = ArgsListOption(example="--grammar-version 1")


def rules():
    from pants.engine.rules import collect_rules
    return collect_rules()
