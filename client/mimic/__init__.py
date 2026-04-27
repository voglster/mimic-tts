"""mimic-tts — Python client for the mimic-tts server."""
from mimic._version import __version__
from mimic.async_client import AsyncClient
from mimic.client import Client

__all__ = ["AsyncClient", "Client", "__version__"]
