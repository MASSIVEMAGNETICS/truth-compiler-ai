#include "truth_ai/heuristic_ranker.hpp"
#include <cmath>
#include <stdexcept>
namespace truth_ai {
ReviewSignal rank_for_review(const DenseNetwork& ranker,const std::vector<double>& features){
  if(features.empty()) throw std::invalid_argument("evidence features must not be empty");
  const auto output=ranker.predict(features);
  if(output.size()!=1||!std::isfinite(output[0])) throw std::runtime_error("invalid heuristic output");
  return {output[0],"REVIEW_ONLY","NON_AUTHORITATIVE_HEURISTIC",ranker.parameter_state_sha256()};
}
}
