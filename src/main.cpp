#include "truth_ai/dense_network.hpp"
#include "truth_ai/victor_adapter.hpp"
#include <iostream>
int main(){truth_ai::DenseNetwork model(2,4,1,1337);truth_ai::CapabilityLease lease{"lease-001","train","truth_ai/model",true};auto receipt=truth_ai::train_and_verify(model,truth_ai::xor_dataset(),{},lease);std::cout<<truth_ai::receipt_json(receipt)<<"\n";return receipt.verified?0:1;}
