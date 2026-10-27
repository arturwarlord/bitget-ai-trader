import unittest

import cost_stress_test as stress


class CostStressTests(unittest.TestCase):
    def test_cost_scenarios_scale_linearly(self):
        scenarios = stress.cost_scenarios(0.16)
        self.assertEqual([row["multiplier"] for row in scenarios], [1.0, 1.5, 2.0])
        self.assertEqual([row["round_trip_cost_pct"] for row in scenarios], [0.16, 0.24, 0.32])

    def test_zero_base_cost_remains_zero(self):
        self.assertTrue(all(row["round_trip_cost_pct"] == 0 for row in stress.cost_scenarios(0)))

    def test_cost_scenario_order_is_monotonic(self):
        costs = [row["round_trip_cost_pct"] for row in stress.cost_scenarios(0.2)]
        self.assertEqual(costs, sorted(costs))
        self.assertGreater(costs[-1], costs[0])


if __name__ == "__main__":
    unittest.main()
