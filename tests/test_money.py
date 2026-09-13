"""Проверки экономики: главное — совпадение с формулой, по которой считали до сих пор."""

import unittest

from envo import money


class Channels(unittest.TestCase):
    def test_afisha_net_share(self):
        # 0,94 × 0,88 − 0,009: этот знаменатель зашит во все прошлые расчёты
        self.assertAlmostEqual(money.AFISHA.net_share, 0.8182, places=4)
        self.assertAlmostEqual(money.AFISHA.net_share - 0.30, 0.5182, places=4)
        self.assertAlmostEqual(money.AFISHA.net_share - 0.40, 0.4182, places=4)
        self.assertAlmostEqual(money.AFISHA.net_share - 0.50, 0.3182, places=4)

    def test_direct_has_no_platform_fee(self):
        self.assertAlmostEqual(money.DIRECT.net_share, 0.871, places=4)
        self.assertAlmostEqual(money.DIRECT.tax_share, 0.12, places=4)

    def test_direct_is_more_profitable(self):
        self.assertGreater(money.DIRECT.net_share, money.AFISHA.net_share)


class NicePrices(unittest.TestCase):
    def test_rounds_up_to_990_above_ten_thousand(self):
        self.assertEqual(money.nice_up(94_100), 94_990)
        self.assertEqual(money.nice_up(10_001), 10_990)

    def test_rounds_up_to_90_below_ten_thousand(self):
        self.assertEqual(money.nice_up(4_100), 4_190)
        self.assertEqual(money.nice_up(8_991), 9_090)

    def test_never_rounds_down(self):
        for value in (1_000, 9_989, 12_345, 99_999):
            self.assertGreaterEqual(money.nice_up(value), value)


class Waves(unittest.TestCase):
    def test_margin_matches_target(self):
        for margin in money.WAVES:
            wave = money.wave_price(39_520, margin)
            actual = money.margin_at_price(wave.payable, wave.cost)
            # округление витрины вверх поднимает маржу, но не опускает
            self.assertGreaterEqual(actual, margin - 1e-9)
            self.assertLess(actual - margin, 0.03)

    def test_listed_price_is_above_payable(self):
        wave = money.wave_price(39_520, 0.45)
        self.assertAlmostEqual(wave.payable, wave.listed * 0.9, places=6)
        self.assertGreater(wave.listed, wave.payable)

    def test_refundable_costs_more(self):
        wave = money.wave_price(39_520, 0.45, refund_premium=0.15)
        self.assertGreater(wave.refundable, wave.payable)

    def test_components_add_up(self):
        wave = money.wave_price(39_520, 0.40)
        total = wave.platform + wave.taxes + wave.xborder + wave.cost + wave.profit
        self.assertAlmostEqual(total, wave.payable, places=6)

    def test_unreachable_margin_is_rejected(self):
        with self.assertRaises(ValueError):
            money.price_for_margin(1000, 0.90)

    def test_ladder_prices_grow(self):
        prices = [w.listed for w in money.ladder(39_520)]
        self.assertEqual(prices, sorted(prices))


class Cost(unittest.TestCase):
    def test_rate_buffer_applies(self):
        cost = money.unit_cost_rub(1499, seller_fee=0.10, rate=22.97)
        self.assertAlmostEqual(cost, 1499 * 1.10 * 23.97, places=6)

    def test_actual_charge_wins_over_rate(self):
        cost = money.unit_cost_rub(1499, rate=22.97, actual_charged=80_000, qty=2)
        self.assertEqual(cost, 40_000)

    def test_weighted_cost(self):
        # 20 по 39 520 и 13 по 39 990
        self.assertAlmostEqual(money.weighted_cost([(20, 39_520), (13, 39_990)]), 39_705.15, places=2)

    def test_weighted_cost_of_nothing(self):
        self.assertEqual(money.weighted_cost([]), 0.0)


class Allocation(unittest.TestCase):
    def test_parts_sum_to_the_charge(self):
        parts = money.allocate_charge(100_000, [30_000, 20_000, 50_000])
        self.assertAlmostEqual(sum(parts), 100_000, places=2)
        self.assertAlmostEqual(parts[0], 30_000, places=2)

    def test_rounding_remainder_goes_to_last_line(self):
        parts = money.allocate_charge(100.0, [1, 1, 1])
        self.assertAlmostEqual(sum(parts), 100.0, places=2)

    def test_empty_purchase_is_rejected(self):
        with self.assertRaises(ValueError):
            money.allocate_charge(100, [0])


class PnL(unittest.TestCase):
    def test_profit_and_frozen_money(self):
        sales = [(94_500, "afisha"), (108_000, "afisha"), (149_000, "direct")]
        pnl = money.event_pnl(sales, invested=2_001_360, cost_of_sold=137_140)
        self.assertAlmostEqual(pnl.revenue, 351_500, places=2)
        # прямая продажа не платит площадке
        self.assertAlmostEqual(pnl.platform, (94_500 + 108_000) * 0.06, places=2)
        self.assertAlmostEqual(pnl.frozen, 2_001_360 - 137_140, places=2)

    def test_write_off_reduces_profit(self):
        sales = [(100_000, "afisha")]
        kept = money.event_pnl(sales, invested=500_000, cost_of_sold=40_000)
        burned = money.event_pnl(sales, invested=500_000, cost_of_sold=40_000, written_off=60_000)
        self.assertAlmostEqual(kept.profit - burned.profit, 60_000, places=2)


if __name__ == "__main__":
    unittest.main()
