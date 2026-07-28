# -*- coding: utf-8 -*-
"""Runtime wrapper that activates the available-data sync service."""
from __future__ import annotations

from typing import Any

from agent.lingxing_available_sync import AvailableLingxingSyncService
from agent.lingxing_controlled_probe import ControlledValidationProbeProvider
from agent.lingxing_integration import create_integrated_app as _create_integrated_app


def create_integrated_app(*args: Any, **kwargs: Any):
    kwargs.setdefault("service_factory", AvailableLingxingSyncService)
    kwargs.setdefault("probe_provider_factory", ControlledValidationProbeProvider)
    return _create_integrated_app(*args, **kwargs)
