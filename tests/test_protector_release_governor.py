import tempfile
import unittest
from pathlib import Path

from protector_release_governor import (
    DeterministicSimulator,
    InvalidTransition,
    LeaseDenied,
    ProtectorReleaseGovernor,
)


class GovernorTests(unittest.TestCase):
    def make_governor(self):
        self.tmp = tempfile.TemporaryDirectory()
        return ProtectorReleaseGovernor(Path(self.tmp.name) / "governor.sqlite3")

    def complete_release(self, governor):
        rid = governor.start_release("Test Release")
        for asset_type in ("audio", "lore", "build"):
            for index in range(10):
                governor.log_asset(rid, asset_type, f"{asset_type}-{index}")
        governor.verify_assets(rid)
        governor.update_lore(rid, "https://example.test/lore")
        governor.link_hub(rid, "https://example.test/hub")
        governor.define_cta(rid, "Join the review queue")
        governor.verify_and_lock(rid)
        return rid

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_strict_state_machine_and_exact_asset_counts(self):
        governor = self.make_governor()
        rid = governor.start_release("Strict")
        with self.assertRaises(InvalidTransition):
            governor.update_lore(rid, "https://example.test/lore")
        for asset_type in ("audio", "lore", "build"):
            for index in range(10):
                governor.log_asset(rid, asset_type, f"{asset_type}-{index}")
        governor.verify_assets(rid)
        with self.assertRaises(InvalidTransition):
            governor.define_cta(rid, "too early")

    def test_hash_chain_detects_tampering_and_survives_restart(self):
        governor = self.make_governor()
        self.complete_release(governor)
        self.assertTrue(governor.verify_receipts())
        db_path = Path(governor.db_path)
        governor.close()
        reopened = ProtectorReleaseGovernor(db_path)
        self.assertTrue(reopened.verify_receipts())
        reopened.conn.execute("UPDATE receipts SET payload='tampered' WHERE sequence=1")
        self.assertFalse(reopened.verify_receipts())

    def test_lease_required_for_device_actions(self):
        governor = self.make_governor()
        with self.assertRaises(LeaseDenied):
            governor.authorize_device_action(None, "victor", "read", "mic:0")
        lease = governor.issue_lease("victor", "device.read", "mic:0", 60)
        receipt = governor.authorize_device_action(lease, "victor", "read", "mic:0")
        self.assertEqual(len(receipt), 64)
        with self.assertRaises(LeaseDenied):
            governor.authorize_device_action(lease, "victor", "read", "camera:0")

    def test_deterministic_simulation(self):
        simulator = DeterministicSimulator(4)
        first = simulator.run(["observe", "review", "defer"], 440)
        second = simulator.run(["observe", "review", "defer"], 440)
        self.assertEqual(first, second)

    def test_malformed_inputs_fail_closed(self):
        governor = self.make_governor()
        with self.assertRaises(ValueError):
            governor.start_release("")
        with self.assertRaises(ValueError):
            governor.issue_lease("victor", "device.read", "mic:0", 0)
        simulator = DeterministicSimulator()
        with self.assertRaises(ValueError):
            simulator.run([], 1)


if __name__ == "__main__":
    unittest.main()

