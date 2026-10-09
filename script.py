"""
Legacy entrypoint and backwards-compatibility shim for Blocket Sniper.
Imports core components from the modularized blocket_sniper package.
"""

import asyncio
from blocket_sniper.client import BlocketIngestor
from blocket_sniper.config import Config, DEFAULT_CONFIG
from blocket_sniper.filters import DeterministicFilter
from blocket_sniper.models import Listing
from blocket_sniper.pipeline import SniperPipeline
from blocket_sniper.storage import StorageManager
from main import main

__all__ = [
    "BlocketIngestor",
    "Config",
    "DEFAULT_CONFIG",
    "DeterministicFilter",
    "Listing",
    "SniperPipeline",
    "StorageManager",
    "main",
]

if __name__ == "__main__":
    asyncio.run(main())