import asyncio
from browser_use_sdk.v3 import AsyncBrowserUse

import os


async def main():
    if not os.getenv("BROWSER_USE_API_KEY"):
        raise SystemExit("Set BROWSER_USE_API_KEY (store it in SSM /apps/courts/key_browser_use)")

    client = AsyncBrowserUse()
    result = await client.run(
        "download the causelist for 03 June 2026 from surpeme court of india website?"
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
