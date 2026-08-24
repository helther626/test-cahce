import asyncio
import traceback

import httpx

from Backend.helper.settings_manager import SettingsManager
from Backend.logger import LOGGER


#----- Periodically self-ping a public endpoint to keep the instance awake
async def ping():
    sleep_time = 1200
    status_url = f"{SettingsManager.current().base_url}/status"

    while True:
        await asyncio.sleep(sleep_time)
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(status_url)
                LOGGER.info(f"Pinged status URL — Status: {resp.status_code}")
        except httpx.TimeoutException:
            LOGGER.warning("Timeout: Could not connect to status URL.")
        except Exception:
            LOGGER.error("Ping failed:\n" + traceback.format_exc())
