"""Copernicus Data Space OAuth2 token management with retry and async lock."""
from __future__ import annotations
import asyncio, time
from dataclasses import dataclass
import httpx
from app.core.config import get_settings
from app.core.exceptions import CopernicusAuthError
from app.core.logging import get_logger

logger = get_logger(__name__)
_TOKEN_REFRESH_BUFFER_SECONDS = 60
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0


@dataclass
class TokenInfo:
    access_token: str; expires_in: int; obtained_at: float

    @property
    def expires_at(self) -> float: return self.obtained_at + self.expires_in
    @property
    def is_valid(self) -> bool: return time.monotonic() < self.expires_at - _TOKEN_REFRESH_BUFFER_SECONDS
    @property
    def seconds_remaining(self) -> float: return max(0.0, self.expires_at - time.monotonic())


class CopernicusTokenRepository:
    def __init__(self) -> None:
        self._token_info: TokenInfo | None = None
        self._refresh_lock = asyncio.Lock()
        self._settings = get_settings()

    def is_configured(self) -> bool:
        return bool(self._settings.copernicus_client_id and self._settings.copernicus_client_secret)

    @property
    def token_info(self) -> TokenInfo | None: return self._token_info

    async def get_token(self) -> str:
        if self._token_info and self._token_info.is_valid:
            return self._token_info.access_token
        async with self._refresh_lock:
            if self._token_info and self._token_info.is_valid:
                return self._token_info.access_token
            return await self._fetch_token_with_retry()

    async def _fetch_token_with_retry(self) -> str:
        if not self.is_configured():
            raise CopernicusAuthError(
                "Copernicus credentials not configured. "
                "Set COPERNICUS_CLIENT_ID and CLIENT_SECRET. "
                "Register free at: https://dataspace.copernicus.eu")
        last_error = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await self._fetch_token()
            except CopernicusAuthError: raise
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning("copernicus_token_retry", attempt=attempt, wait=wait, error=str(exc))
                    await asyncio.sleep(wait)
        raise CopernicusAuthError(f"Token fetch failed after {_MAX_RETRIES} attempts: {last_error}")

    async def _fetch_token(self) -> str:
        s = self._settings
        payload = {"grant_type": "client_credentials",
                   "client_id": s.copernicus_client_id,
                   "client_secret": s.copernicus_client_secret}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(s.copernicus_token_url, data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        if response.status_code == 401:
            raise CopernicusAuthError("Copernicus auth 401 — check CLIENT_ID and CLIENT_SECRET.")
        if response.status_code == 400:
            raise CopernicusAuthError(f"Copernicus auth 400: {response.text[:200]}")
        if not response.is_success:
            raise CopernicusAuthError(f"Copernicus auth {response.status_code}: {response.text[:200]}")
        data = response.json()
        if "access_token" not in data:
            raise CopernicusAuthError(f"No access_token in response: {data}")
        expires_in = int(data.get("expires_in", 600))
        self._token_info = TokenInfo(data["access_token"], expires_in, time.monotonic())
        logger.info("copernicus_token_obtained", expires_in=expires_in)
        return self._token_info.access_token
