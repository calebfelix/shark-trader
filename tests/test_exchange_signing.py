import json

from shark.exchange import ExchangeClient


class TestJsNumberSafe:
    def test_whole_number_float_becomes_int(self):
        # Regression: JS's JSON.stringify(46101.0) -> "46101" (no decimal
        # point), but Python's json.dumps(46101.0) -> "46101.0" - this
        # mismatch broke live order signing (CLAUDE.md 4c). Converting
        # whole-number floats to int before dumps must match JS's output.
        assert ExchangeClient._js_number_safe(46101.0) == 46101
        assert isinstance(ExchangeClient._js_number_safe(46101.0), int)

    def test_fractional_float_stays_float(self):
        assert ExchangeClient._js_number_safe(0.0035) == 0.0035
        assert isinstance(ExchangeClient._js_number_safe(0.0035), float)

    def test_recurses_into_dict(self):
        result = ExchangeClient._js_number_safe({"price": 100.0, "qty": 0.0035})
        assert result == {"price": 100, "qty": 0.0035}
        assert isinstance(result["price"], int)

    def test_recurses_into_list(self):
        result = ExchangeClient._js_number_safe([1.0, 2.5, {"x": 3.0}])
        assert result == [1, 2.5, {"x": 3}]

    def test_non_numeric_values_untouched(self):
        result = ExchangeClient._js_number_safe({"side": "BUY", "reduceOnly": False})
        assert result == {"side": "BUY", "reduceOnly": False}


class TestSignedRequestBytesMatchSignedString:
    def test_sign_is_deterministic_hmac(self):
        client = ExchangeClient("https://example.test", api_key="k", api_secret="s")
        sig1 = client._sign("hello")
        sig2 = client._sign("hello")
        assert sig1 == sig2
        assert len(sig1) == 64  # sha256 hexdigest length

    def test_different_secrets_produce_different_signatures(self):
        client_a = ExchangeClient("https://example.test", api_key="k", api_secret="secret-a")
        client_b = ExchangeClient("https://example.test", api_key="k", api_secret="secret-b")
        assert client_a._sign("hello") != client_b._sign("hello")

    def test_signed_string_uses_compact_separators(self):
        # Regression: requests.post(json=params) re-serializes the body
        # with different spacing than what gets signed if separators
        # aren't pinned - see CLAUDE.md 4b. Confirms the exact
        # serialization convention signed_post/_delete rely on.
        params = ExchangeClient._js_number_safe({"price": 46101.0, "qty": 0.002})
        signed_string = json.dumps(params, separators=(",", ":"))
        assert " " not in signed_string
        assert signed_string == '{"price":46101,"qty":0.002}'
