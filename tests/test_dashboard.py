import pytest

from dashboard import compute_stats, current_position


def _trade(net, time="2026-01-01T10:00:00", fees=0.0):
    return {"time": time, "net": net, "gross": net + fees, "fees": fees}


class TestComputeStats:
    def test_no_trades(self):
        stats, curve = compute_stats([], 1000.0)
        assert stats["equity"] == 1000.0
        assert stats["trades"] == 0
        assert stats["win_rate"] is None
        assert curve == []

    def test_equity_is_start_plus_cumulative_net(self):
        stats, curve = compute_stats([_trade(10), _trade(-4), _trade(6)], 1000.0)
        assert stats["equity"] == pytest.approx(1012.0)
        assert [p["equity"] for p in curve] == pytest.approx([1010.0, 1006.0, 1012.0])

    def test_win_rate_and_profit_factor(self):
        stats, _ = compute_stats([_trade(10), _trade(10), _trade(-5)], 1000.0)
        assert stats["wins"] == 2 and stats["losses"] == 1
        assert stats["win_rate"] == pytest.approx(200 / 3)
        assert stats["profit_factor"] == pytest.approx(4.0)

    def test_profit_factor_none_when_no_losses(self):
        stats, _ = compute_stats([_trade(10)], 1000.0)
        assert stats["profit_factor"] is None

    def test_max_drawdown_measured_from_running_peak(self):
        # 1000 -> 1100 (peak) -> 990 : drawdown 110/1100 = 10%
        stats, _ = compute_stats([_trade(100), _trade(-110)], 1000.0)
        assert stats["max_drawdown_pct"] == pytest.approx(10.0)

    def test_fees_summed(self):
        stats, _ = compute_stats([_trade(5, fees=1.0), _trade(-2, fees=0.5)], 1000.0)
        assert stats["fees"] == pytest.approx(1.5)


class TestCurrentPosition:
    def test_no_output(self):
        assert current_position([])["status_line"] is None

    def test_flat(self):
        p = current_position(["price=100.00 SAR=98.00 trend=up position=None"])
        assert p["side"] is None

    def test_open_long_picks_up_entry_detail(self):
        lines = [
            "    entry=98.05 SL=96.09 TP=103.93",
            "price=97.95 SAR=96.02 trend=up position=LONG",
        ]
        p = current_position(lines)
        assert p["side"] == "LONG"
        assert p["detail"] == "entry=98.05 SL=96.09 TP=103.93"


class TestBotProcessMatching:
    def test_matches_run_command(self):
        from dashboard import is_bot_cmdline
        assert is_bot_cmdline(["python", "-u", "main.py", "run", "--strategy", "psar_trend"])
        assert is_bot_cmdline(["/usr/bin/python3", "main.py", "--config", "c.json", "run"])

    def test_ignores_other_commands(self):
        from dashboard import is_bot_cmdline
        assert not is_bot_cmdline(["python", "main.py", "backtest", "--strategy", "x"])
        assert not is_bot_cmdline(["python", "dashboard.py"])
        assert not is_bot_cmdline(["python", "verify_order_endpoint.py"])
        assert not is_bot_cmdline(None)


class TestThemeToggle:
    def test_every_page_has_the_toggle_and_a_manual_dark_override(self):
        import dashboard as d
        for name in ("PAGE", "SETTINGS_PAGE", "HEALTH_PAGE"):
            html = getattr(d, name)
            assert "themebtn" in html, name
            assert '[data-theme="dark"]' in html and ':not([data-theme="light"])' in html, name
            assert "localStorage" in html and "shark-theme" in html, name

    def test_storage_failure_is_harmless(self):
        # localStorage can throw (private window, blocked storage): the toggle must still work
        import dashboard as d
        assert 'try { return localStorage.getItem(KEY); } catch(e){ return null; }' in d.PAGE
        assert 'try { localStorage.setItem(KEY, n); } catch(e){}' in d.PAGE


class TestRunningStrategy:
    def test_reads_strategy_from_cmdline(self, monkeypatch):
        import dashboard

        class P:
            def cmdline(self):
                return ["python", "-u", "main.py", "run", "--strategy", "mean_reversion_v2"]
        monkeypatch.setattr(dashboard.psutil, "Process", lambda pid: P())
        assert dashboard.running_strategy([123]) == "mean_reversion_v2"

    def test_none_when_not_running(self):
        import dashboard
        assert dashboard.running_strategy([]) is None
