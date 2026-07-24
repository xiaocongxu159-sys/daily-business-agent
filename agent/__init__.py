"""Windows-local agent for the daily business data engine."""

from agent.settings import AgentSettings


def create_app(*args, **kwargs):
    """Import the FastAPI application lazily to keep lightweight modules usable."""
    from agent.app import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["AgentSettings", "create_app"]
