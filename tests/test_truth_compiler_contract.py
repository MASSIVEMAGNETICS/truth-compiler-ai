import hashlib
import json
import subprocess
import sys
import unittest

from truth_compiler_contract import SCHEMA_VERSION, TruthCompiler, TruthVerdict


def prov(text: str):
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def request(evidence, **policy):
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_id": "claim:test",
        "claim": {"type": "candidate_action", "id": "candidate:a"},
        "evidence": evidence,
        "policy": {
            "authority_allowed": True,
            "min_independent_support": 1,
            "max_contradictions": 0,
            "required_fact_keys": ["owner_authorized"],
            **policy,
        },
    }


class TruthCompilerContractTests(unittest.TestCase):
    def test_verified_requires_provenance_independence_and_required_fact(self):
        result = TruthCompiler().compile(request([
            {
                "evidence_id": "e1",
                "status": "SUPPORTED",
                "source": "launch_gate",
                "independence_group": "owner_authority",
                "provenance": prov("owner-authority"),
                "facts": {"owner_authorized": True},
            }
        ]))
        self.assertEqual(result.verdict, TruthVerdict.VERIFIED)
        self.assertEqual(result.independent_support_groups, ("owner_authority",))
        self.assertEqual(len(result.result_sha256), 64)

    def test_correlated_support_is_not_double_counted(self):
        evidence = [
            {
                "evidence_id": f"e{i}",
                "status": "SUPPORTED",
                "source": "same-source",
                "independence_group": "same-origin",
                "provenance": prov(f"row-{i}"),
                "facts": {"owner_authorized": True},
            }
            for i in range(2)
        ]
        result = TruthCompiler().compile(request(evidence, min_independent_support=2))
        self.assertEqual(result.verdict, TruthVerdict.UNKNOWN)
        self.assertEqual(result.independent_support_groups, ("same-origin",))
        self.assertIn("insufficient_independent_support", result.reasons)

    def test_unknown_and_missing_provenance_fail_closed(self):
        evidence = [{
            "evidence_id": "e1",
            "status": "UNKNOWN",
            "source": "sensor",
            "independence_group": "sensor:1",
            "provenance": {},
            "facts": {},
        }]
        result = TruthCompiler().compile(request(evidence))
        self.assertEqual(result.verdict, TruthVerdict.UNKNOWN)
        self.assertIn("e1", result.invalid_provenance_ids)
        self.assertIn("e1", result.unknown_evidence_ids)

    def test_policy_denial_is_distinct_from_evidence_unknown(self):
        evidence = [{
            "evidence_id": "e1",
            "status": "SUPPORTED",
            "source": "owner",
            "independence_group": "owner",
            "provenance": prov("owner"),
            "facts": {"owner_authorized": True},
        }]
        result = TruthCompiler().compile(request(evidence, authority_allowed=False))
        self.assertEqual(result.verdict, TruthVerdict.DENIED)
        self.assertEqual(result.policy_state, "DENIED")

    def test_cli_round_trip(self):
        payload = request([{
            "evidence_id": "e1",
            "status": "SUPPORTED",
            "source": "owner",
            "independence_group": "owner",
            "provenance": prov("owner"),
            "facts": {"owner_authorized": True},
        }])
        proc = subprocess.run(
            [sys.executable, "truth_compiler_contract.py"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        row = json.loads(proc.stdout)
        self.assertEqual(row["verdict"], "VERIFIED")
        self.assertEqual(row["claim_id"], "claim:test")


if __name__ == "__main__":
    unittest.main()
