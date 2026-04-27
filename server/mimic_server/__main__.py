"""Console entry: `mimic-server` runs uvicorn with env-driven settings."""

from __future__ import annotations

import uvicorn

from mimic_server.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "mimic_server.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
