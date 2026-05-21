"""
MEXC contract API HTTP client.
Extracted from app.py so lib/ modules can call it without circular imports.
"""
from __future__ import annotations

import sys
import time

import requests

MEXC_BASE = "https://contract.mexc.com/api/v1"
_TIMEOUT  = 10


def fetch_mexc(path: str, params: dict | None = None) -> dict | list | None:
    """
    GET a MEXC contract API endpoint and unwrap the response envelope.
    Returns body["data"] or None on any failure.
    Retries up to 3 times on connection errors and 5xx responses.
    """
    last_err = ""
    for attempt in range(3):
        try:
            resp = requests.get(f"{MEXC_BASE}{path}", params=params, timeout=_TIMEOUT)
            if resp.status_code >= 500 and attempt < 2:
                last_err = f"HTTP {resp.status_code}"
                time.sleep(attempt + 1)
                continue
            resp.raise_for_status()
            body = resp.json()
            if not body.get("success"):
                return None
            return body.get("data")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
        except Exception as e:
            print(f"[mexc_client] fetch error [{path}]: {e}", file=sys.stderr)
            return None
    print(f"[mexc_client] fetch error [{path}]: {last_err} (gave up after 3 attempts)", file=sys.stderr)
    return None
