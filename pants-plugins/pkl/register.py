"""Core registration for the PKL Pants plugin backend."""

from pkl import dependency_inference as _dep_inference
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
    ]
