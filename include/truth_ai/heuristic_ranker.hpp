#pragma once

#include "truth_ai/dense_network.hpp"
#include <string>
#include <vector>

namespace truth_ai {

struct ReviewSignal {
  double score{0.0};
  std::string decision{"REVIEW_ONLY"};
  std::string basis{"NON_AUTHORITATIVE_HEURISTIC"};
  std::string parameter_state_sha256;
};

// Scores evidence for triage. It cannot emit PASS, FAIL, UNKNOWN, or authorize action.
ReviewSignal rank_for_review(const DenseNetwork& ranker,
                             const std::vector<double>& evidence_features);

}  // namespace truth_ai
