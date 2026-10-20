"""Shark Exchange API client - klines (public) and HMAC-signed order endpoints.

Confirmed against the live API on 2026-09-15: /v1/market/klines is POST
with a "pair" field (not GET + "symbol"), and returns a list of dicts
with open/high/low/close/volume as strings. See CLAUDE.md for the sweep
that found this.
"""

import hashlib
import hmac
import json
import time

import requests


class ExchangeClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = ""):
        self.base_url = base_url
        self.api_key = api_key
        self.api_secret = api_secret

    def _sign(self, payload_str: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _js_number_safe(obj):
        """The docs' own signing example is JS's JSON.stringify(params) -
        this backend is almost certainly Node.js, reconstructing the
        signing string server-side by re-parsing and re-serializing the
        body (common when a JSON body-parsing middleware runs before
        signature checks, so raw bytes aren't available to hash). JS has
        no int/float distinction: JSON.stringify(46101.0) -> "46101", no
        decimal point - but Python's json.dumps(46101.0) -> "46101.0".
        Recursively convert any whole-number float to an int before
        dumping, so our serialization matches what a JS re-serialization
        would produce, regardless of which fields happen to be whole
        numbers on a given request."""
        if isinstance(obj, float) and obj.is_integer():
            return int(obj)
        if isinstance(obj, dict):
            return {k: ExchangeClient._js_number_safe(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ExchangeClient._js_number_safe(v) for v in obj]
        return obj

    @staticmethod
    def _raise_with_body(resp: requests.Response) -> None:
        """requests' default raise_for_status() discards the response
        body, which is exactly where the exchange puts the useful
        validation error - re-raise with it included."""
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as err:
            raise requests.exceptions.HTTPError(f"{err} | response body: {resp.text}", response=resp) from None

    def signed_get(self, endpoint: str, params: dict | None = None) -> dict:
        params = self._js_number_safe(dict(params or {}))
        params["timestamp"] = str(int(time.time() * 1000))
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        signature = self._sign(query_string)
        headers = {"api-key": self.api_key, "signature": signature}
        resp = requests.get(f"{self.base_url}{endpoint}?{query_string}", headers=headers, timeout=15)
        self._raise_with_body(resp)
        return resp.json()

    def signed_post(self, endpoint: str, params: dict) -> dict:
        params = self._js_number_safe(dict(params))
        params["timestamp"] = str(int(time.time() * 1000))
        # Sign and send the EXACT same bytes - requests.post(json=params) would
        # re-serialize the body itself (different spacing than our compact
        # separators), so the signed string and the wire bytes could silently
        # diverge. Serialize once, sign that, send that via data= instead.
        # _js_number_safe avoids a related mismatch: the backend is almost
        # certainly Node.js (docs' own signing example is JS's
        # JSON.stringify), which renders whole-number floats without a
        # decimal point (46101, not 46101.0) - see its docstring.
        data_to_sign = json.dumps(params, separators=(",", ":"))
        signature = self._sign(data_to_sign)
        headers = {"api-key": self.api_key, "signature": signature, "Content-Type": "application/json"}
        resp = requests.post(f"{self.base_url}{endpoint}", data=data_to_sign, headers=headers)
        self._raise_with_body(resp)
        return resp.json()

    _MAX_CANDLES_PER_CALL = 1500  # confirmed live: the API silently caps any larger "limit" to this

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list[dict]:
        """Public endpoint, no auth. Returns candles oldest-first as dicts
        with open/high/low/close/volume floats.

        Paginates automatically via endTime when limit exceeds the API's
        per-call cap (confirmed 1500) - each page's oldest candle becomes
        the next page's endTime cutoff, walking backward through history.
        Stops early if the exchange has no more history to return.
        """
        url = f"{self.base_url}/v1/market/klines"
        all_candles: list[dict] = []
        end_time: int | None = None
        remaining = limit

        while remaining > 0:
            batch_limit = min(remaining, self._MAX_CANDLES_PER_CALL)
            body = {"pair": symbol, "interval": interval, "limit": batch_limit}
            if end_time is not None:
                body["endTime"] = end_time
            resp = requests.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break

            batch = [{
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0.0)),
            } for c in data]
            all_candles = batch + all_candles

            remaining -= len(batch)
            if len(batch) < batch_limit:
                break  # exchange ran out of history before we hit our target
            end_time = int(data[0]["startTime"]) - 1

        return all_candles

    def get_exchange_info(self, symbol: str) -> dict:
        """Public endpoint, no auth. Returns the trading constraints for
        one contract: min_qty, min_notional, quantity_precision,
        max_leverage. Confirmed live 2026-09-15 via
        /v1/exchange/exchangeInfo - see CLAUDE.md."""
        url = f"{self.base_url}/v1/exchange/exchangeInfo"
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()

        for contract in data.get("contracts", []):
            if contract.get("name") == symbol:
                filters = {f["filterType"]: f for f in contract.get("filters", [])}
                return {
                    "min_qty": float(filters.get("LIMIT_QTY_SIZE", {}).get("minQty", 0)),
                    "min_notional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 0)),
                    "quantity_precision": int(contract.get("quantityPrecision", 3)),
                    "max_leverage": float(contract.get("maxLeverage", 1)),
                    "base_asset": contract.get("baseAsset", ""),
                    "quote_asset": contract.get("quoteAsset", ""),
                    # Max fraction a LIMIT order's price may deviate from
                    # market (e.g. 0.15 = 15%) - confirmed live 2026-09-15
                    # via error 3009 "Limit price is out of allowed values".
                    "limit_price_var_allowed": float(contract.get("limitPriceVarAllowed", 0.1)),
                }
        raise ValueError(f"Symbol '{symbol}' not found in Shark Exchange's exchangeInfo contracts")

    def get_last_price(self, symbol: str) -> float:
        """Most recent close for symbol, via a 1-candle klines call."""
        candles = self.get_klines(symbol, "1m", 1)
        if not candles:
            raise ValueError(f"No price data returned for {symbol}")
        return candles[-1]["close"]

    def signed_delete(self, endpoint: str, params: dict) -> dict:
        params = self._js_number_safe(dict(params))
        params["timestamp"] = str(int(time.time() * 1000))
        data_to_sign = json.dumps(params, separators=(",", ":"))  # see signed_post's comment
        signature = self._sign(data_to_sign)
        headers = {"api-key": self.api_key, "signature": signature, "Content-Type": "application/json"}
        resp = requests.delete(f"{self.base_url}{endpoint}", data=data_to_sign, headers=headers)
        self._raise_with_body(resp)
        return resp.json()

    def cancel_order(self, client_order_id: str) -> dict:
        return self.signed_delete("/v1/order/delete-order", {"clientOrderId": client_order_id})

    def get_open_orders(self, symbol: str | None = None) -> dict:
        params = {"symbol": symbol} if symbol else None
        return self.signed_get("/v1/order/open-orders", params)

    def get_futures_wallet(self, margin_asset: str = "INR") -> dict:
        """Read-only. Futures wallet balances via GET /v1/wallet/futures-wallet/details
        (signed). Returns floats; unrealised P&L is cross + isolated combined."""
        resp = self.signed_get("/v1/wallet/futures-wallet/details", {"marginAsset": margin_asset})
        data = resp["data"] if isinstance(resp.get("data"), dict) else resp
        if "walletBalance" not in data:
            raise ValueError("Unexpected wallet response shape (no walletBalance field)")

        def num(key):
            try:
                return float(data[key])
            except (KeyError, TypeError, ValueError):
                return None

        pnl_parts = [num("unrealisedPnlCross"), num("unrealisedPnlIsolated")]
        known = [v for v in pnl_parts if v is not None]
        return {
            "wallet_balance": num("walletBalance"),
            "withdrawable": num("withdrawableBalance"),
            "locked": num("lockedBalance"),
            "margin_balance": num("marginBalance"),
            "unrealised_pnl": sum(known) if known else None,
            "asset": data.get("marginAsset", margin_asset),
        }

    def get_positions(self, position_status: str, symbol: str | None = None) -> dict:
        """Read-only. position_status is one of OPEN/CLOSED/LIQUIDATED."""
        params = {"symbol": symbol} if symbol else {}
        return self.signed_get(f"/v1/positions/{position_status}", params)

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str) -> dict:
        """Sets leverage/margin mode for a symbol - this is an account-wide
        per-symbol setting the exchange uses for every order placed on
        that symbol afterward (NOT a per-order field). Must be called
        before placing orders that rely on this leverage. See CLAUDE.md."""
        params = {"leverage": leverage, "marginMode": margin_mode, "contractName": symbol}
        return self.signed_post("/v1/exchange/update/preference", params)

    @staticmethod
    def _as_list(resp) -> list:
        """Shark returns a bare list for positions/open-orders, but tolerate a {"data": [...]} wrapper."""
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict):
            for key in ("data", "orders", "positions"):
                if isinstance(resp.get(key), list):
                    return resp[key]
        return []

    def get_open_position(self, symbol: str) -> dict | None:
        """The open position on `symbol`, normalised to
        {side, qty, entry_price, position_id}, or None when flat. Raises on any
        API/shape problem so callers never mistake an error for 'flat'.
        Field names (contractPair, positionType, positionAmount/quantity,
        entryPrice, positionId) are from docs.sharkexchange.in."""
        for pos in self._as_list(self.get_positions("OPEN", symbol)):
            if pos.get("contractPair", pos.get("symbol")) != symbol:
                continue
            if str(pos.get("positionStatus", "OPEN")).upper() != "OPEN":
                continue
            qty = abs(float(pos.get("positionAmount", pos.get("quantity", 0)) or 0))
            if qty <= 0:
                continue
            side = str(pos.get("positionType", "")).upper()
            if side not in ("LONG", "SHORT"):
                raise ValueError(f"Unexpected positionType in position: {pos!r}")
            return {"side": side, "qty": qty, "entry_price": float(pos["entryPrice"]),
                    "position_id": pos.get("positionId")}
        return None

    def list_open_orders(self, symbol: str) -> list[dict]:
        return [o for o in self._as_list(self.get_open_orders(symbol))
                if o.get("symbol", symbol) == symbol]
