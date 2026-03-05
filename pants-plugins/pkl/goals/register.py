"""Backend registration for `pkl.goals.test`."""

from pkl.goals import test as test_module


def rules():
    return [
        *test_module.rules(),
    ]
