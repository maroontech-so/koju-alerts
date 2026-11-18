"""Offline parser tests. Run: python -m unittest -v test_signal_parser.py"""
import unittest

from signal_parser import parse_signals, valid_signals


class TestParser(unittest.TestCase):
    def test_simple_sell(self):
        text = "SELL XAUUSD @ 3898.00\nTP1. 3883.00\nTP2.3853.00\nSL. 3919.00"
        signals = valid_signals(text)
        self.assertEqual(len(signals), 1)
        s = signals[0]
        self.assertEqual(s["direction"], "SELL")
        self.assertEqual(s["instrument"], "XAUUSD")
        self.assertAlmostEqual(s["entry"], 3898.0)
        self.assertAlmostEqual(s["stop_loss"], 3919.0)
        self.assertEqual(len(s["take_profit"]), 2)

    def test_sell_stop_order(self):
        text = "SELL STOP XAUUSD@3888.15\nTP1@3873.15\nTP2@3843.15\nSL@3903.15"
        s = valid_signals(text)[0]
        self.assertEqual(s["order_type"], "STOP")
        self.assertAlmostEqual(s["entry"], 3888.15)

    def test_comma_thousands(self):
        text = "BUY XAUUSD @ 4,111.36\nSL 4,050.00\nTP1 4,200.00"
        s = valid_signals(text)[0]
        self.assertAlmostEqual(s["entry"], 4111.36)
        self.assertAlmostEqual(s["stop_loss"], 4050.0)

    def test_buy_geometry_rejected_when_sl_above_entry(self):
        # SL above entry on a BUY is invalid.
        text = "BUY EURUSD @ 1.0850\nSL 1.0900\nTP1 1.0950"
        self.assertEqual(valid_signals(text), [])
        parsed = parse_signals(text)[0]
        self.assertFalse(parsed["complete"])
        self.assertIn("below entry", parsed["reason"])

    def test_sell_geometry_rejected_when_tp_above_entry(self):
        text = "SELL EURUSD @ 1.0850\nSL 1.0800\nTP1 1.0900"
        self.assertEqual(valid_signals(text), [])

    def test_missing_sl_rejected(self):
        text = "BUY EURUSD @ 1.0850\nTP1 1.0950"
        self.assertEqual(valid_signals(text), [])

    def test_missing_tp_rejected(self):
        text = "BUY EURUSD @ 1.0850\nSL 1.0800"
        self.assertEqual(valid_signals(text), [])

    def test_multi_trade_post(self):
        text = (
            "SELL XAUUSD @ 3898.00\nSL 3919.00\nTP1 3883.00\nTP2 3853.00\n\n"
            "BUY EURUSD @ 1.0850\nSL 1.0800\nTP1 1.0900\nTP2 1.0950"
        )
        sigs = valid_signals(text)
        self.assertEqual(len(sigs), 2)
        self.assertEqual(sigs[0]["direction"], "SELL")
        self.assertEqual(sigs[1]["direction"], "BUY")

    def test_non_signal_text_returns_empty(self):
        text = "Good morning everyone, markets are choppy today."
        self.assertEqual(valid_signals(text), [])

    
    def test_duplicate_tp_deduplicated(self):
        text = "SELL XAUUSD @ 3900\nSL 3920\nTP1 3880\nTP1 3880\nTP2 3870"
        s = valid_signals(text)[0]
        self.assertEqual(s["take_profit"], [3880.0, 3870.0])

    def test_comma_thousands_anglo(self):
        text = "BUY XAUUSD @ 4,111.36\nSL 4,050.00\nTP1 4,200.00"
        s = valid_signals(text)[0]
        self.assertAlmostEqual(s["entry"], 4111.36)
        self.assertAlmostEqual(s["stop_loss"], 4050.0)
        self.assertAlmostEqual(s["take_profit"][0], 4200.0)

    def test_comma_thousands_european(self):
        text = "BUY XAUUSD @ 4.111,36\nSL 4.050,00\nTP1 4.200,00"
        s = valid_signals(text)[0]
        self.assertAlmostEqual(s["entry"], 4111.36)
        self.assertAlmostEqual(s["stop_loss"], 4050.0)

    def test_large_comma_only_thousands(self):
        # BTC-style price: "BTCUSDT @ 68,432" — comma is thousands, not decimal.
        text = "BUY BTCUSDT @ 68,432\nSL 66,000\nTP1 72,000"
        s = valid_signals(text)[0]
        self.assertAlmostEqual(s["entry"], 68432.0)
        self.assertAlmostEqual(s["stop_loss"], 66000.0)

    def test_forex_decimal_dot_not_thousands(self):
        # 1.0850 must remain 1.0850, not 10850.
        text = "BUY EURUSD @ 1.0850\nSL 1.0800\nTP1 1.0950"
        s = valid_signals(text)[0]
        self.assertAlmostEqual(s["entry"], 1.0850)
        self.assertAlmostEqual(s["stop_loss"], 1.0800)


if __name__ == "__main__":
    unittest.main()