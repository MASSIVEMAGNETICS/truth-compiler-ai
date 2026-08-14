#include "truth_ai/dense_network.hpp"
#include "truth_ai/sha256.hpp"
#include <cmath>
#include <random>
#include <sstream>
#include <stdexcept>

namespace truth_ai {
DenseNetwork::DenseNetwork(std::size_t in,std::size_t hidden,std::size_t out,std::uint64_t seed):input_size_(in),hidden_size_(hidden),output_size_(out),w1_(in*hidden),b1_(hidden),w2_(hidden*out),b2_(out) {
  if(!in||!hidden||!out) throw std::invalid_argument("network dimensions must be non-zero");
  std::mt19937_64 rng(seed);
  const double w1_limit = std::sqrt(6.0 / static_cast<double>(in + hidden));
  const double w2_limit = std::sqrt(6.0 / static_cast<double>(hidden + out));
  std::uniform_real_distribution<double> d1(-w1_limit, w1_limit);
  std::uniform_real_distribution<double> d2(-w2_limit, w2_limit);
  for (auto& v : w1_) v = d1(rng);
  for (auto& v : w2_) v = d2(rng);
}
double DenseNetwork::sigmoid(double x) noexcept { if(x>=0){double z=std::exp(-x);return 1.0/(1.0+z);} double z=std::exp(x);return z/(1.0+z); }
double DenseNetwork::checked(double v,const char* op){ if(!std::isfinite(v)) throw std::overflow_error(std::string("non-finite value in ")+op); return v; }
void DenseNetwork::validate_input(const std::vector<double>& in) const { if(in.size()!=input_size_) throw std::invalid_argument("input dimension mismatch"); for(double v:in) checked(v,"input"); }
void DenseNetwork::validate_sample(const Sample& sample) const { validate_input(sample.input); if(sample.target.size()!=output_size_) throw std::invalid_argument("target dimension mismatch"); for(double v:sample.target) checked(v,"target"); }
void DenseNetwork::validate_config(const TrainingConfig& c) const { if(c.epochs==0||!(c.learning_rate>0)||!std::isfinite(c.learning_rate)||c.convergence_loss<0||!std::isfinite(c.convergence_loss)) throw std::invalid_argument("invalid training configuration"); }
std::vector<double> DenseNetwork::forward_hidden(const std::vector<double>& in) const { validate_input(in); std::vector<double> h(hidden_size_); for(std::size_t j=0;j<hidden_size_;++j){double z=b1_[j];for(std::size_t i=0;i<input_size_;++i)z+=in[i]*w1_[j*input_size_+i];h[j]=sigmoid(checked(z,"hidden activation"));} return h; }
std::vector<double> DenseNetwork::predict(const std::vector<double>& in) const { auto h=forward_hidden(in); std::vector<double> y(output_size_);for(std::size_t k=0;k<output_size_;++k){double z=b2_[k];for(std::size_t j=0;j<hidden_size_;++j)z+=h[j]*w2_[k*hidden_size_+j];y[k]=sigmoid(checked(z,"output activation"));}return y; }
double DenseNetwork::loss(const std::vector<Sample>& samples) const { if(samples.empty()) throw std::invalid_argument("samples must not be empty"); double total=0;for(const auto&s:samples){validate_sample(s);auto y=predict(s.input);for(std::size_t k=0;k<output_size_;++k){double e=y[k]-s.target[k];total+=e*e;}}const double count=static_cast<double>(samples.size())*static_cast<double>(output_size_);return checked(total/(2.0*count),"loss"); }
TrainingReport DenseNetwork::train(const std::vector<Sample>& samples,const TrainingConfig& c){if(samples.empty())throw std::invalid_argument("samples must not be empty");validate_config(c);for(const auto&s:samples)validate_sample(s);TrainingReport r; r.seed=c.seed;r.initial_loss=loss(samples);
  for(std::size_t epoch=0;epoch<c.epochs;++epoch){for(const auto&s:samples){auto h=forward_hidden(s.input);auto y=predict(s.input);std::vector<double> d2(output_size_);for(std::size_t k=0;k<output_size_;++k)d2[k]=(y[k]-s.target[k])*y[k]*(1-y[k]);std::vector<double>d1(hidden_size_);for(std::size_t j=0;j<hidden_size_;++j){double g=0;for(std::size_t k=0;k<output_size_;++k)g+=d2[k]*w2_[k*hidden_size_+j];d1[j]=g*h[j]*(1-h[j]);}for(std::size_t k=0;k<output_size_;++k){for(std::size_t j=0;j<hidden_size_;++j)w2_[k*hidden_size_+j]=checked(w2_[k*hidden_size_+j]-c.learning_rate*d2[k]*h[j],"weight update");b2_[k]=checked(b2_[k]-c.learning_rate*d2[k],"bias update");}for(std::size_t j=0;j<hidden_size_;++j){for(std::size_t i=0;i<input_size_;++i)w1_[j*input_size_+i]=checked(w1_[j*input_size_+i]-c.learning_rate*d1[j]*s.input[i],"weight update");b1_[j]=checked(b1_[j]-c.learning_rate*d1[j],"bias update");}}r.epochs_run=epoch+1;r.final_loss=loss(samples);if(r.final_loss<=c.convergence_loss){r.converged=true;break;}}r.parameter_state_sha256=parameter_state_sha256();return r;}
bool DenseNetwork::finite() const noexcept {for(double v:w1_)if(!std::isfinite(v))return false;for(double v:b1_)if(!std::isfinite(v))return false;for(double v:w2_)if(!std::isfinite(v))return false;for(double v:b2_)if(!std::isfinite(v))return false;return true;}
std::string DenseNetwork::parameter_state_sha256() const {std::ostringstream s;s<<input_size_<<":"<<hidden_size_<<":"<<output_size_;s.precision(17);for(double v:w1_)s<<","<<v;for(double v:b1_)s<<","<<v;for(double v:w2_)s<<","<<v;for(double v:b2_)s<<","<<v;return sha256(s.str());}
std::vector<Sample> xor_dataset(){return {{{0,0},{0}},{{0,1},{1}},{{1,0},{1}},{{1,1},{0}}};}
}
