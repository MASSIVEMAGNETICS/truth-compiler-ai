#pragma once

#include "truth_ai/dense_network.hpp"
#include <string>

namespace truth_ai {

struct CapabilityLease {
  std::string lease_id;
  std::string operation;
  std::string resource;
  bool approved{false};
};

struct VerificationReceipt {
  std::string event_id;
  std::string parameter_state_sha256;
  std::string dataset_sha256;
  std::string parent_hash;
  std::string receipt_hash;
  bool verified{false};
  bool production_authorized{false};
  std::string status;
};

VerificationReceipt train_and_verify(DenseNetwork& model,
                                     const std::vector<Sample>& samples,
                                     const TrainingConfig& config,
                                     const CapabilityLease& lease,
                                     const std::string& parent_hash = "GENESIS");
std::string receipt_json(const VerificationReceipt& receipt);

}  // namespace truth_ai
