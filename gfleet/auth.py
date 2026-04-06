import base64
import time
import logging
import requests

from config.settings import GFLEET_BASE_URL, GFLEET_USERNAME, GFLEET_PASSWORD, GFLEET_API_KEY

logger = logging.getLogger(__name__)


class GFleetAuthError(Exception):
    pass


class GFleetAuthenticator:
    """
    Manages Bearer token lifecycle.
    Tokens are cached in memory and refreshed on expiry or 401 response.
    Tokens are treated as valid for 1 hour (conservative default).
    """

    TOKEN_TTL_SECONDS = 3600

    def __init__(self):
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def _encode_credentials(self) -> str:
        raw = f"{GFLEET_USERNAME}:{GFLEET_PASSWORD}"
        return base64.b64encode(raw.encode()).decode()

    def get_token(self, force_refresh: bool = False) -> str:
        if force_refresh or self._token is None or time.monotonic() >= self._token_expiry:
            self._token, self._token_expiry = self._fetch_token()
        return self._token

    def _fetch_token(self) -> tuple[str, float]:
        url = f"{GFLEET_BASE_URL}/auth/get-token"
        headers = {
            "Authorization": f"Basic {self._encode_credentials()}",
            "x-api-key": GFLEET_API_KEY,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise GFleetAuthError(f"Failed to fetch GFleet token: {e}") from e

        data = resp.json()
        if data.get("responseCode") != "000":
            raise GFleetAuthError(f"GFleet auth error: {data}")

        token = data.get("detail")
        if not token:
            raise GFleetAuthError(f"No token in response: {data}")

        expiry = time.monotonic() + self.TOKEN_TTL_SECONDS
        logger.info("GFleet token acquired.")
        return token, expiry
