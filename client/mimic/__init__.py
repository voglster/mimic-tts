"""mimic-tts — Python client for the mimic-tts server."""
from mimic._version import __version__
from mimic.client import Client

__all__ = ["Client", "__version__"]
