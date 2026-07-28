# -*- coding: utf-8 -*-
"""Runtime wrapper that selects the controlled Lingxing probe provider."""
from __future__ import annotations

from typing import Any

from agent.lingxing_controlled_probe import ControlledValidationProbeProvider
from agent.lingxing_integration import create_integrated_app as _create_integrated_app


def create_integrated_app(*args: Any, **kwargs: Any):
    kwargs.setdefault("probe_provider_factory", ControlledValidationProbeProvider)
    return _create_integrated_app(*args, **kwargs)
