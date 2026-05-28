"""FastAPI demo app for the HITL approval queue.

Routes are split into HTML pages (Jinja-rendered) and a parallel JSON API
under `/api/...` so the queue can be driven by either a reviewer in a
browser or another service.

Run via `make hitl-serve`.
"""

from p2p_agent.hitl.webapp.server import app, create_app

__all__ = ["app", "create_app"]
