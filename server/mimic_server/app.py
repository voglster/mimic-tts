"""mimic-tts server — FastAPI app factory."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from mimic_server.auth import install_error_handler, make_caller_dependency
from mimic_server.backends import TTSBackend, make_backend
from mimic_server.bootstrap import bootstrap
from mimic_server.config import Settings
from mimic_server.routes import clones, openai, system, tts
from mimic_server.services import Services
from mimic_server.usage import UsageTracker

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
                "upstream access control is enforced.",
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


def _make_lifespan(backend: TTSBackend, settings: Settings) -> Any:
    """Build the FastAPI lifespan that supervises backend + optional Wyoming."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio

        tasks = [asyncio.create_task(backend.run_lifecycle())]
        if settings.wyoming_enabled:
            from mimic_server.wyoming_server import run_wyoming_server

            tasks.append(asyncio.create_task(run_wyoming_server(backend, settings)))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            backend.unload()

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
    boot = bootstrap(settings)
    svc = Services(
        settings=settings,
        backend=backend,
        db=boot.db,
        keys=boot.keys,
        voices=boot.voices,
        usage=UsageTracker(boot.db),
        root=boot.root,
        caller=Depends(make_caller_dependency(settings, boot.keys, boot.root)),
    )
    app = FastAPI(title="mimic-tts API", lifespan=_make_lifespan(backend, settings))
    install_error_handler(app)
    for module in (system, tts, clones, openai):
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


# Default app for `uvicorn mimic_server.app:app` and the console entry.
app = build_app(Settings())
