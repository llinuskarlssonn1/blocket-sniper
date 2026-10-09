"""
Module execution entrypoint: python -m blocket_sniper
"""

import asyncio
from main import main

if __name__ == "__main__":
    asyncio.run(main())
