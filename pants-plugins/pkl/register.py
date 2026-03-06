"""Core registration for the PKL Pants plugin backend.

This module registers the minimum set of rules (subsystem, target types,
process helper, and dependency inference) that are shared by all PKL goals.
Goal-specific and lint backends are kept in separate sub-packages so that
users can opt-in only to the features they need.

Full set of available backends for ``pants.toml``::

    backend_packages = [
        "pkl",                          # core: target types + dep inference
        "pkl.goals",                    # pants test + pants package + pants tailor
        "pkl.lint.eval_check",          # pants lint  (pkl eval validation)
        "pkl.lint.fmt",                 # pants fmt   (pkl format)
    ]

Register only ``"pkl"`` if you need target types and dependency inference
without any goals, or register all four for the full feature set.

Note: ``"pkl"`` must always be listed before the other backends because the
goal and lint backends depend on the target types defined here.
"""

from pants.core.util_rules import system_binaries as _system_binaries

from pkl import dependency_inference as _dep_inference
from pkl import pkl_dependencies as _pkl_dependencies
from pkl import pkl_process as _pkl_process
from pkl import subsystem as _subsystem
from pkl import target_types as _target_types


def target_types():
    return [
        _target_types.PklSourceTarget,
        _target_types.PklSourcesTarget,
        _target_types.PklTestTarget,
        _target_types.PklTestsTarget,
        _target_types.PklPackageTarget,
    ]


def rules():
    return [
        *_subsystem.rules(),
        *_target_types.rules(),
        *_pkl_process.rules(),
        *_dep_inference.rules(),
        *_pkl_dependencies.rules(),
        *_system_binaries.rules(),
    ]
