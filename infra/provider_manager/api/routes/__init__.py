"""API Routes for Provider Manager."""
from .providers import router as providers_router
from .groups import router as groups_router
from .health import router as health_router
from .jobs import router as jobs_router

__all__ = ["providers_router", "groups_router", "health_router", "jobs_router"]
