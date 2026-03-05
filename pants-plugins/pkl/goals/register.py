"""Backend registration for `pkl.goals` (test + package + tailor)."""

from pkl.goals import package as package_module
from pkl.goals import tailor as tailor_module
from pkl.goals import test as test_module


def rules():
    return [
        *test_module.rules(),
        *package_module.rules(),
        *tailor_module.rules(),
    ]
