"""REST API routers (M2.8): task/session lifecycle + SSE. M3.1 adds read-only query
endpoints (GET /sessions/{id}, GET /tasks, GET /modes, GET /agents/templates). See app.py
for how these are mounted and how their dependency seams (SandboxProvider, the LLM seams)
are wired."""
from .agents import router as agents_router
from .attachments import router as attachments_router
from .export import router as export_router
from .model_catalog import router as model_catalog_router
from .app_config import router as app_config_router
from .modes import router as modes_router
from .pull_request import router as pull_request_router
from .search import router as search_router
from .tasks import router as tasks_router
from .sessions import router as sessions_router

__all__ = ["tasks_router", "sessions_router", "modes_router", "agents_router", "attachments_router",
           "pull_request_router", "model_catalog_router", "app_config_router", "export_router", "search_router"]
