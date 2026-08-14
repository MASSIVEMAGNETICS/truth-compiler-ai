#include "truth_ai/dense_network.hpp"
#include "truth_ai/sha256.hpp"
#include "truth_ai/victor_adapter.hpp"
#include "truth_ai/heuristic_ranker.hpp"
#ifdef TRUTH_BUNDLED_TEST_RUNNER
#include <cmath>
#include <iostream>
#include <stdexcept>
#define EXPECT_TRUE(x) do{if(!(x))throw std::runtime_error("EXPECT_TRUE failed: " #x);}while(0)
#define EXPECT_NEAR(a,b,e) do{if(std::abs((a)-(b))>(e))throw std::runtime_error("EXPECT_NEAR failed");}while(0)
int main(){try{auto d=truth_ai::xor_dataset();truth_ai::DenseNetwork n(2,4,1,1337);auto before=n.loss(d);auto r=n.train(d);EXPECT_TRUE(r.converged);EXPECT_TRUE(r.final_loss<before);EXPECT_TRUE(n.finite());EXPECT_NEAR(n.predict({0,0})[0],0,0.2);EXPECT_NEAR(n.predict({0,1})[0],1,0.2);EXPECT_NEAR(n.predict({1,0})[0],1,0.2);EXPECT_NEAR(n.predict({1,1})[0],0,0.2);auto x=truth_ai::sha256("abc");EXPECT_TRUE(x=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");truth_ai::CapabilityLease bad{"x","write","other",false};auto rr=truth_ai::train_and_verify(n,d,{},bad);EXPECT_TRUE(!rr.verified&&rr.status=="BLOCKED_CAPABILITY_LEASE");bool malformed=false;try{n.loss({{{0,1},{}}});}catch(const std::invalid_argument&){malformed=true;}EXPECT_TRUE(malformed);truth_ai::DenseNetwork ranker(2,3,1);auto signal=truth_ai::rank_for_review(ranker,{0.1,0.2});EXPECT_TRUE(signal.decision=="REVIEW_ONLY");std::cout<<"all bundled tests passed\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
#else
#include <gtest/gtest.h>
TEST(DenseNetwork, LearnsXorAndStaysFinite){auto d=truth_ai::xor_dataset();truth_ai::DenseNetwork n(2,4,1,1337);auto before=n.loss(d);auto r=n.train(d);EXPECT_TRUE(r.converged);EXPECT_LT(r.final_loss,before);EXPECT_TRUE(n.finite());EXPECT_NEAR(n.predict({0,0})[0],0,0.2);EXPECT_NEAR(n.predict({0,1})[0],1,0.2);EXPECT_NEAR(n.predict({1,0})[0],1,0.2);EXPECT_NEAR(n.predict({1,1})[0],0,0.2);}
TEST(TruthCompilerEvidence, HashAndLeaseAreEnforced){EXPECT_EQ(truth_ai::sha256("abc"),"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");truth_ai::DenseNetwork n(2,4,1);auto rr=truth_ai::train_and_verify(n,truth_ai::xor_dataset(),{}, {"x","write","other",false});EXPECT_FALSE(rr.verified);EXPECT_EQ(rr.status,"BLOCKED_CAPABILITY_LEASE");}
TEST(TruthCompilerEvidence, HeuristicCanOnlyPrioritizeReview){truth_ai::DenseNetwork n(2,3,1);auto signal=truth_ai::rank_for_review(n,{0.1,0.2});EXPECT_EQ(signal.decision,"REVIEW_ONLY");EXPECT_EQ(signal.basis,"NON_AUTHORITATIVE_HEURISTIC");}
#endif
