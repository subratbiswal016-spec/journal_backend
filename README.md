# Option OI Dashboard + Trading Journal

A FastAPI app with two parts in one repo:

- **Dashboard** — live NIFTY / BankNIFTY / FinNIFTY / Sensex option **Open Interest**
  (strike-wise Call/Put OI, change-in-OI, PCR, Max Pain, support/resistance).
- **Journal** — log/sync your trades (Fyers, Dhan, CSV), with analytics, a
  custom date-range filter, psychology/weekday insights, CSV export, and
  auto-generated plain-English insights.

Data sources are swappable via `DATA_SOURCE`: `fyers` (official, free),
`upstox`, `mock` (demo, no login), or `nse` (unofficial).

## Project layout

```
backend/     FastAPI app, data sources, journal (SQLite) + analytics
frontend/    static dashboard (HTML/CSS/JS), served by the backend at /
tests/       pytest suite for the analytics
render.yaml  one-click Render deploy
```

## Run locally (Windows)

```bash
run.bat
```

Then open http://127.0.0.1:8787. Or manually:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.app:app --host 127.0.0.1 --port 8787
```

Set `DATA_SOURCE=mock` in `.env` to see the demo without logging in. For live
data use `fyers` (see below). Run tests with:

```bash
.venv\Scripts\python.exe -m pytest -q
```

## One-click deploy (Render, free)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/subratbiswal016-spec/journal_backend)

Or on render.com: **New → Blueprint → pick this repo** (it reads `render.yaml`).

After the first deploy:

1. It starts on **mock** data so the dashboard loads immediately.
2. Set a **`JOURNAL_PASSWORD`** in the Render dashboard (the app is public —
   this gates it behind a password).
3. To use **live Fyers data**: set `DATA_SOURCE=fyers`, `FYERS_APP_ID`,
   `FYERS_SECRET_ID`, and `FYERS_REDIRECT_URI=https://YOUR-APP.onrender.com/auth/callback`
   in the Render dashboard — then set that **exact** redirect URL in your Fyers
   app at https://myapi.fyers.in/dashboard/ and click **Login with Fyers**.

### Data persistence

The free plan's filesystem is **ephemeral**, so the journal DB resets on
redeploy. That's usually fine — just re-sync from Fyers/Dhan. To keep data
permanently, add a Render **persistent disk** and set `JOURNAL_DB=/data/.journal.db`
(see the commented block in `render.yaml`).

## Configuration

| Setting | Meaning |
|---|---|
| `DATA_SOURCE` | `fyers`, `upstox`, `mock`, or `nse` |
| `JOURNAL_PASSWORD` | Optional password gate (blank = off) |
| `JOURNAL_DB` | DB file path (defaults to project root; set to a disk path to persist) |
| `FYERS_APP_ID` / `FYERS_SECRET_ID` | Fyers app credentials |
| `*_REDIRECT_URI` | Must match the Redirect URL set in your broker app |
| `REFRESH_SECONDS` | How often the server refreshes from the source |

## Notes

- Markets are open 9:15–15:30 IST. Outside those hours the feed returns the last
  snapshot.
- This is a personal tool. **Not investment advice.**
