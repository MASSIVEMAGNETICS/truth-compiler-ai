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
    def valid_evidence(self):
        return [{
            "evidence_id": "e1",
            "status": "SUPPORTED",
            "source": "owner",
            "independence_group": "owner",
            "provenance": prov("owner"),
            "facts": {"owner_authorized": True},
        }]

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

    def test_duplicate_evidence_identity_is_rejected(self):
        evidence = [
            {
                "evidence_id": "e1",
                "status": "SUPPORTED",
                "source": "owner",
                "independence_group": "owner",
                "provenance": prov("owner-support"),
                "facts": {"owner_authorized": True},
            },
            {
                "evidence_id": "e1",
                "status": "CONTRADICTED",
                "source": "other",
                "independence_group": "other",
                "provenance": prov("owner-contradiction"),
                "facts": {"owner_authorized": False},
            },
        ]
        with self.assertRaisesRegex(ValueError, "duplicate evidence_id"):
            TruthCompiler().compile(request(evidence))

    def test_blank_evidence_identity_is_rejected(self):
        evidence = [{
            "evidence_id": "   ",
            "status": "SUPPORTED",
            "source": "owner",
            "independence_group": "owner",
            "provenance": prov("owner"),
            "facts": {"owner_authorized": True},
        }]
        with self.assertRaisesRegex(ValueError, "evidence_id is required"):
            TruthCompiler().compile(request(evidence))

    def test_malformed_authority_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "authority_allowed must be a boolean"):
            TruthCompiler().compile(request(self.valid_evidence(), authority_allowed="false"))

    def test_policy_thresholds_require_native_integers(self):
        malformed_values = ("2", 1.5, True, None)
        for value in malformed_values:
            with self.subTest(min_independent_support=value):
                with self.assertRaisesRegex(ValueError, "min_independent_support must be an integer"):
                    TruthCompiler().compile(request(self.valid_evidence(), min_independent_support=value))

        for value in malformed_values:
            with self.subTest(max_contradictions=value):
                with self.assertRaisesRegex(ValueError, "max_contradictions must be an integer"):
                    TruthCompiler().compile(request(self.valid_evidence(), max_contradictions=value))

    def test_policy_thresholds_reject_out_of_range_values(self):
        with self.assertRaisesRegex(ValueError, "min_independent_support must be >= 1"):
            TruthCompiler().compile(request(self.valid_evidence(), min_independent_support=0))
        with self.assertRaisesRegex(ValueError, "max_contradictions must be >= 0"):
            TruthCompiler().compile(request(self.valid_evidence(), max_contradictions=-1))

    def test_required_fact_keys_require_list_of_non_empty_strings(self):
        with self.assertRaisesRegex(ValueError, "required_fact_keys must be a list of strings"):
            TruthCompiler().compile(request(self.valid_evidence(), required_fact_keys="owner_authorized"))
        with self.assertRaisesRegex(ValueError, "required_fact_keys must contain non-empty strings"):
            TruthCompiler().compile(request(self.valid_evidence(), required_fact_keys=["owner_authorized", " "]))
        with self.assertRaisesRegex(ValueError, "required_fact_keys must contain non-empty strings"):
            TruthCompiler().compile(request(self.valid_evidence(), required_fact_keys=["owner_authorized", 7]))

    def test_required_fact_keys_are_normalized_deterministically(self):
        result = TruthCompiler().compile(request(
            self.valid_evidence(),
            required_fact_keys=[" owner_authorized ", "owner_authorized"],
        ))
        self.assertEqual(result.verdict, TruthVerdict.VERIFIED)
        self.assertEqual(result.missing_fact_keys, ())

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
        result = TruthCompiler().compile(request(self.valid_evidence(), authority_allowed=False))
        self.assertEqual(result.verdict, TruthVerdict.DENIED)
        self.assertEqual(result.policy_state, "DENIED")

    def test_cli_round_trip(self):
        payload = request(self.valid_evidence())
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
