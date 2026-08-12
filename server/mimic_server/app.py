"""mimic-tts server — FastAPI app factory."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mimic_server.auth import install_error_handler
from mimic_server.backends import TTSBackend, make_backend
from mimic_server.config import Settings
from mimic_server.routes import admin, clones, openai, system, tts
from mimic_server.services import Services, assemble_services

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _configure_environment(settings: Settings) -> None:
    """Apply environment-level settings (logging, HF_HOME, dirs) and enforce
    the public-bind-needs-auth safety check."""
    logging.basicConfig(level=settings.log_level)
    if settings.model_cache is not None:
        import os

        os.environ["HF_HOME"] = str(settings.model_cache)
    settings.reference_dir.mkdir(parents=True, exist_ok=True)
    _check_public_bind_auth(settings)


def _check_public_bind_auth(settings: Settings) -> None:
    """Refuse to start when bound to a non-loopback host without a bearer token.

    Set MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 to override (e.g. when a
    reverse proxy / Tailscale ACL is providing access control upstream).
    """
    is_loopback = settings.host in {"127.0.0.1", "::1", "localhost"}
    if is_loopback or settings.api_token or settings.allow_unauthenticated_public_bind:
        if settings.api_token:
            logger.info("bearer auth ON (MIMIC_API_TOKEN set)")
        elif settings.allow_unauthenticated_public_bind:
            logger.warning(
                "auth OFF and host=%s (public). "
                "MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 was set — assuming "
                "upstream access control is enforced. Anonymous callers resolve "
                "to a non-admin identity, so /admin/* routes are unavailable "
                "without a token, and share root's quota (capped at root's "
                "max_voices/daily_char_quota, with no in-band way to raise it). "
                "They also share root's key id, so they own root's voices and "
                "can delete or publish them.",
                settings.host,
            )
        else:
            logger.info("auth OFF (loopback-only bind)")
        return
    raise RuntimeError(
        f"refusing to start: host={settings.host!r} is publicly reachable but "
        "MIMIC_API_TOKEN is not set. Set MIMIC_API_TOKEN to enable bearer auth, "
        "bind to 127.0.0.1, or set MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 "
        "if access control is enforced upstream (reverse proxy, tailnet ACL)."
    )


def _make_lifespan(svc: Services) -> Any:
    """Build the FastAPI lifespan that supervises backend + optional Wyoming."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio

        tasks = [asyncio.create_task(svc.backend.run_lifecycle())]
        if svc.settings.wyoming_enabled:
            from mimic_server.wyoming_server import run_wyoming_server

            tasks.append(asyncio.create_task(run_wyoming_server(svc)))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            svc.backend.unload()

    return lifespan


def build_app(
    settings: Settings,
    backend_factory: Callable[[Settings], TTSBackend] | None = None,
) -> FastAPI:
    """Construct the FastAPI app with injected settings and backend.

    `backend_factory` is for tests; production uses `make_backend(settings)`
    which dispatches on `settings.backend`.
    """

    _configure_environment(settings)
    backend = (backend_factory or make_backend)(settings)
    svc = assemble_services(settings, backend)
    # A reachable server must not publish its schema — /admin/* would
    # otherwise be fully documented to anyone who hits /openapi.json, which
    # needs no token (docs routes are unauthenticated by FastAPI's own
    # design). Gate on reachability, not just on auth being configured:
    # MIMIC_ALLOW_UNAUTHENTICATED_PUBLIC_BIND=1 (the containerized default —
    # see _default_host()) is host=0.0.0.0 with api_token=None, which is
    # internet-reachable with docs wide open under an auth-only check. Only
    # the genuinely local, no-token dev case keeps them on.
    is_loopback = settings.host in {"127.0.0.1", "::1", "localhost"}
    docs_kwargs: dict[str, Any] = (
        {}
        if not settings.api_token and is_loopback
        else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    )
    app = FastAPI(title="mimic-tts API", lifespan=_make_lifespan(svc), **docs_kwargs)
    install_error_handler(app)
    for module in (system, tts, clones, openai, admin):
        module.register(app, svc)
    _mount_web_ui(app)
    return app


def _mount_web_ui(app: FastAPI) -> None:
    """If MIMIC_WEB_DIST points at a built UI directory, serve it at '/'.

    StaticFiles is mounted LAST so the API routes registered above take
    precedence in route matching — `/health`, `/voices`, etc. still resolve
    to the handlers, not to files in the dist tree.
    """
    web_dist = os.environ.get("MIMIC_WEB_DIST", "")
    if not web_dist:
        return
    dist_path = Path(web_dist)
    if not dist_path.is_dir():
        logger.warning("MIMIC_WEB_DIST=%s does not exist; skipping web UI mount", web_dist)
        return
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="web")
    logger.info("serving web UI from %s", dist_path)


_lazy_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazily build the default app on first access of `mimic_server.app.app`.

    `uvicorn mimic_server.app:app` and the `mimic-server` console entry both
    resolve `app` via attribute access on this module, so PEP 562's
    module-level `__getattr__` keeps both working unchanged while making a
    bare `import mimic_server.app` a no-op. Building it eagerly at import
    time — the previous behavior — ran the full bootstrap migration (opened
    the DB, seeded the root key, moved voice files) as a side effect of
    merely importing the module, which broke anything that imports this
    module without intending to serve, e.g. `python -c "import
    mimic_server.app"` or a test collecting this file.
    """
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    global _lazy_app
    if _lazy_app is None:
        _lazy_app = build_app(Settings())
    return _lazy_app
