cmake_minimum_required(VERSION 3.16)
project(truth_compiler_ai VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
option(TRUTH_BUILD_TESTS "Build substantive tests" ON)
option(TRUTH_ENABLE_SANITIZERS "Enable Address/Undefined sanitizers" OFF)

if(TRUTH_ENABLE_SANITIZERS AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -Wall -Wextra -Wpedantic)
  add_link_options(-fsanitize=address,undefined)
endif()

add_library(truth_ai STATIC
  src/dense_network.cpp
  src/sha256.cpp
  src/victor_adapter.cpp
  src/heuristic_ranker.cpp
)
target_include_directories(truth_ai PUBLIC include)
target_compile_options(truth_ai PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)

add_executable(truth_ai_demo src/main.cpp)
target_link_libraries(truth_ai_demo PRIVATE truth_ai)

if(TRUTH_BUILD_TESTS)
  enable_testing()
  find_package(GTest QUIET)
  if(GTest_FOUND)
    add_executable(truth_ai_tests tests/test_dense_network.cpp)
    target_link_libraries(truth_ai_tests PRIVATE truth_ai GTest::gtest_main)
    include(GoogleTest)
    gtest_discover_tests(truth_ai_tests)
  else()
    message(STATUS "GTest not installed; building the bundled substantive test runner")
    add_executable(truth_ai_tests tests/test_dense_network.cpp)
    target_compile_definitions(truth_ai_tests PRIVATE TRUTH_BUNDLED_TEST_RUNNER=1)
    target_link_libraries(truth_ai_tests PRIVATE truth_ai)
    add_test(NAME truth_ai_tests COMMAND truth_ai_tests)
  endif()
endif()

install(TARGETS truth_ai truth_ai_demo ARCHIVE DESTINATION lib RUNTIME DESTINATION bin)
install(DIRECTORY include/ DESTINATION include)
