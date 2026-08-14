#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace truth_ai {

struct Sample {
  std::vector<double> input;
  std::vector<double> target;
};

struct TrainingConfig {
  std::size_t epochs{5000};
  double learning_rate{0.7};
  std::uint64_t seed{1337};
  double convergence_loss{0.01};
};

struct TrainingReport {
  std::size_t epochs_run{0};
  double initial_loss{0.0};
  double final_loss{0.0};
  bool converged{false};
  std::uint64_t seed{0};
  std::string parameter_state_sha256;
};

class DenseNetwork {
 public:
  DenseNetwork(std::size_t input_size, std::size_t hidden_size,
               std::size_t output_size, std::uint64_t seed = 1337);

  std::vector<double> predict(const std::vector<double>& input) const;
  TrainingReport train(const std::vector<Sample>& samples,
                       const TrainingConfig& config = {});
  double loss(const std::vector<Sample>& samples) const;
  bool finite() const noexcept;
  std::string parameter_state_sha256() const;
  std::size_t input_size() const noexcept { return input_size_; }
  std::size_t hidden_size() const noexcept { return hidden_size_; }
  std::size_t output_size() const noexcept { return output_size_; }

 private:
  std::size_t input_size_;
  std::size_t hidden_size_;
  std::size_t output_size_;
  std::vector<double> w1_, b1_, w2_, b2_;
  void validate_input(const std::vector<double>& input) const;
  void validate_sample(const Sample& sample) const;
  void validate_config(const TrainingConfig& config) const;
  static double sigmoid(double x) noexcept;
  static double checked(double value, const char* operation);
  std::vector<double> forward_hidden(const std::vector<double>& input) const;
};

std::vector<Sample> xor_dataset();

}  // namespace truth_ai
