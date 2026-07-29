# -*- coding: utf-8 -*-
"""Runtime wrapper for controlled probes and available business-data sync."""
from __future__ import annotations

from typing import Any

from agent.lingxing_available_sync import AvailableLingxingSyncService
from agent.lingxing_controlled_probe import ControlledValidationProbeProvider
from agent.lingxing_dashboard_state import recover_interrupted_dashboard_jobs
from agent.lingxing_dashboard_worker import run_dashboard_job_isolated
import agent.lingxing_integration as base_integration


def create_integrated_app(*args: Any, **kwargs: Any):
    kwargs.setdefault("probe_provider_factory", ControlledValidationProbeProvider)
    kwargs.setdefault("service_factory", AvailableLingxingSyncService)

    # The base endpoint resolves this module global when the request runs. Keep
    # its public API unchanged while moving the heavy dashboard pipeline into a
    # bounded child process.
    base_integration.execute_lingxing_dashboard_job = run_dashboard_job_isolated
    app = base_integration.create_integrated_app(*args, **kwargs)
    recover_interrupted_dashboard_jobs(app.state.store)
    return app
