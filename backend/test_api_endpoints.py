import unittest
from demo_risk_api import risk_policy, risk_health, risk_preview
from demo_dashboard_api import get_demo_account

class DirectApiEndpointAuditTests(unittest.TestCase):
    def test_risk_policy_endpoint(self):
        data = risk_policy()
        self.assertEqual(data["environment"], "BYBIT_DEMO")
        self.assertEqual(data["risk_percent"], 1.0)
        self.assertEqual(data["max_open_and_pending"], 3)
        self.assertEqual(data["mode"], "DRY_RUN_ONLY")
        self.assertFalse(data["trading_enabled"])
        self.assertFalse(data["real_trading_enabled"])
        self.assertEqual(data["order_execution"], "ORDER_EXECUTION_DISABLED")
        self.assertIn("capacity_policy", data)
        self.assertEqual(data["capacity_policy"], "Maximum 3 combined active position legs and distinct pending entry order commitments")

    def test_risk_health_endpoint(self):
        data = risk_health()
        self.assertIn(data["status"], ("VALID", "DEGRADED", "UNAVAILABLE"))
        self.assertEqual(data["environment"], "BYBIT_DEMO")
        self.assertEqual(data["mode"], "DRY_RUN_ONLY")
        self.assertFalse(data["trading_enabled"])
        self.assertEqual(data["order_execution"], "DISABLED / NO ORDERS")

    def test_risk_preview_endpoint(self):
        data = risk_preview()
        self.assertEqual(data["environment"], "BYBIT_DEMO")
        self.assertEqual(data["mode"], "DRY_RUN_ONLY")
        self.assertFalse(data["trading_enabled"])
        self.assertEqual(data["order_execution"], "ORDER_EXECUTION_DISABLED")
        self.assertIn("candidates", data)

    def test_account_endpoint(self):
        data = get_demo_account()
        self.assertEqual(data["environment"], "BYBIT_DEMO")
        self.assertIn(data["execution_status"], ["DISABLED / NO ORDERS", "ACTIVE / BYBIT DEMO (1% RISK)"])
        self.assertEqual(data["max_open_and_pending"], 3)
        self.assertFalse(data["trading_enabled"])

if __name__ == "__main__":
    unittest.main()
