# Truth Compiler assessment — target state

| Dimension | State | Evidence |
|---|---|---|
| Source | PRESENT | `src/` and `include/` contain a real C++17 implementation |
| Provenance | VERIFIED ORIGINAL / ATTRIBUTION REQUIRED | `PROVENANCE.md`, MIT license |
| Build definition | PRESENT | CMake 3.16+ |
| Implementation | PRESENT | Dense layer, sigmoid, MSE loss, backpropagation, training, non-authoritative review ranker |
| Tests | PRESENT / SUBSTANTIVE | XOR regression, SHA-256 known vector, capability denial |
| Learning behavior | TESTED | XOR convergence is asserted |
| Numerical defenses | IMPLEMENTED | finite checks, stable sigmoid, overflow exceptions |
| CI configuration | PRESENT | GCC/Clang, sanitizers, Valgrind, clang-tidy, Docker |
| Fork CI | NOT APPLICABLE | This package is original, not a fork |
| Upstream CI | NOT APPLICABLE | No upstream dependency is claimed |
| Upstream security | NOT APPLICABLE | No upstream dependency is claimed |
| Upstream static analysis | NOT APPLICABLE | No upstream dependency is claimed |
| Production authorization | NOT GRANTED | Explicit fail-closed adapter and policy boundary |
| Victor integration | PRESENT / BOUNDED | `victor_adapter.hpp`; emits a hash-chained VerificationReceipt; no authority grant |

## Governing laws

`PREDICTION != PROVEN`. AI may prioritize evidence; AI may never manufacture evidence. The ranker can emit only `REVIEW_ONLY` and never PASS, FAIL, UNKNOWN, authorization, or merge decisions. `parameter_state_sha256` identifies serialized parameters; it is not authentication or provenance.

## Reproduction

```sh
cmake -S . -B build
cmake --build build --parallel
ctest --test-dir build --output-on-failure
./build/truth_ai_demo
```
