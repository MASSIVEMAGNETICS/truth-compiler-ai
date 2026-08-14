# Protector / Release Governor

This adapter is a bounded control-plane component for Truth Compiler and Victor.

It enforces workflow state, exact release asset gates, capability leases, and an
append-only SQLite WAL receipt chain. It does not decide truth, issue production
authorization, or convert perception/simulation output into evidence.

Run the dependency-free tests from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_protector_release_governor.py' -v
```

The simulation component is reproducible from an integer seed and is suitable
only for ranking hypotheses for review. Device actions require a matching,
unexpired lease for the exact subject, capability, and device scope.

