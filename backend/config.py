"""Runtime configuration, loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val not in (None, "") else default


DATA_SOURCE = _get("DATA_SOURCE", "nse").lower()
# Lower floor of 3s so the dashboard can run near real-time. Keep a floor to
# stay within the broker's rate limits (OI itself doesn't update sub-second).
REFRESH_SECONDS = max(3, int(_get("REFRESH_SECONDS", "10")))
SYMBOLS = [s.strip().upper() for s in _get("SYMBOLS", "NIFTY,BANKNIFTY,FINNIFTY,SENSEX").split(",") if s.strip()]

# Shared OAuth callback path (both brokers redirect here)
_DEFAULT_REDIRECT = "http://127.0.0.1:8787/auth/callback"

# Upstox
UPSTOX_API_KEY = _get("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = _get("UPSTOX_API_SECRET", "")
UPSTOX_REDIRECT_URI = _get("UPSTOX_REDIRECT_URI", _DEFAULT_REDIRECT)
UPSTOX_ACCESS_TOKEN = _get("UPSTOX_ACCESS_TOKEN", "")

# Dhan (trading journal sync) — usually set via the UI, stored in the journal DB
DHAN_CLIENT_ID = _get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = _get("DHAN_ACCESS_TOKEN", "")

# Fyers
FYERS_APP_ID = _get("FYERS_APP_ID", "")
FYERS_SECRET_ID = _get("FYERS_SECRET_ID", "")
FYERS_REDIRECT_URI = _get("FYERS_REDIRECT_URI", _DEFAULT_REDIRECT)
FYERS_ACCESS_TOKEN = _get("FYERS_ACCESS_TOKEN", "")
