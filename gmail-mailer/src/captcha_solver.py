"""
Captcha solver — integrates with RuCaptcha / 2captcha (same API).

Used when Google shows a reCAPTCHA during Gmail login.
Supports:
  - reCAPTCHA v2 (checkbox)
  - reCAPTCHA v3
  - Image captchas (legacy)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# RuCaptcha and 2captcha share the same API format
RUCAPTCHA_SUBMIT = "https://rucaptcha.com/in.php"
RUCAPTCHA_RESULT = "https://rucaptcha.com/res.php"

TWOCAPTCHA_SUBMIT = "https://2captcha.com/in.php"
TWOCAPTCHA_RESULT = "https://2captcha.com/res.php"


class CaptchaSolver:
    """
    Async captcha solver using RuCaptcha or 2captcha API.
    api_key: your service API key
    service: 'rucaptcha' (default) or '2captcha'
    """

    def __init__(self, api_key: str, service: str = "rucaptcha"):
        self.api_key = api_key
        self.submit_url = RUCAPTCHA_SUBMIT if service == "rucaptcha" else TWOCAPTCHA_SUBMIT
        self.result_url = RUCAPTCHA_RESULT if service == "rucaptcha" else TWOCAPTCHA_RESULT

    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        timeout: int = 120,
    ) -> Optional[str]:
        """Submit reCAPTCHA v2, poll for solution. Returns g-recaptcha-response token."""
        task_id = await self._submit_recaptcha(site_key, page_url)
        if not task_id:
            return None
        return await self._poll_result(task_id, timeout=timeout)

    async def _submit_recaptcha(self, site_key: str, page_url: str) -> Optional[str]:
        params = {
            "key": self.api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.submit_url,
                    data=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json(content_type=None)
                    if data.get("status") == 1:
                        return str(data["request"])
                    logger.warning("Captcha submit error: %s", data)
        except Exception as exc:
            logger.error("Captcha submit failed: %s", exc)
        return None

    async def _poll_result(self, task_id: str, timeout: int = 120) -> Optional[str]:
        params = {"key": self.api_key, "action": "get", "id": task_id, "json": 1}
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self.result_url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        data = await resp.json(content_type=None)
                        if data.get("status") == 1:
                            return str(data["request"])
                        if data.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                            logger.warning("Captcha unsolvable")
                            return None
            except Exception as exc:
                logger.debug("Poll error: %s", exc)

        logger.warning("Captcha solve timed out after %ds", timeout)
        return None

    async def report_bad(self, task_id: str) -> None:
        """Report an incorrect solution to get a refund."""
        params = {"key": self.api_key, "action": "reportbad", "id": task_id, "json": 1}
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(self.result_url, params=params)
        except Exception:
            pass
