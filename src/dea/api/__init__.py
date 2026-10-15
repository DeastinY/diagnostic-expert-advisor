"""HTTP API over the DEA core (FastAPI). ``dea serve`` starts it with the web frontend."""

from dea.api.app import create_app

__all__ = ["create_app"]
