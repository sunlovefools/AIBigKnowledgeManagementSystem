"""
Master test runner for all query module tests
Runs tests for all service modules: query_refiner, answer_generator, and integration tests
"""
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parents[2]   # 到 backend/
sys.path.insert(0, str(BACKEND_DIR))

# Import test modules
from tests.queryTests import test_query_refiner
from tests.queryTests import test_answer_generator
from tests.queryTests import test_query


def print_header(title):
    """Print a formatted header"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70 + "\n")


async def run_all_query_tests():
    """Run all query-related tests"""
    print_header("QUERY MODULE COMPREHENSIVE TESTING SUITE")
    
    all_results = []
    
    # Test 1: Query Refiner
    print("\n" + "🔍 " + "="*65)
    print("   MODULE 1: QUERY REFINER")
    print("="*70)
    try:
        await test_query_refiner.run_all_tests()
        all_results.append(("Query Refiner", True))
    except Exception as e:
        print(f"\n❌ Query Refiner tests failed with error: {e}\n")
        all_results.append(("Query Refiner", False))
    
    # Test 2: Answer Generator
    print("\n" + "🧠 " + "="*65)
    print("   MODULE 2: ANSWER GENERATOR")
    print("="*70)
    try:
        await test_answer_generator.run_all_tests()
        all_results.append(("Answer Generator", True))
    except Exception as e:
        print(f"\n❌ Answer Generator tests failed with error: {e}\n")
        all_results.append(("Answer Generator", False))
    
    # Test 3: Query Integration
    print("\n" + "🔗 " + "="*65)
    print("   MODULE 3: QUERY INTEGRATION (END-TO-END)")
    print("="*70)
    try:
        await test_query.run_all_tests()
        all_results.append(("Query Integration", True))
    except Exception as e:
        print(f"\n❌ Query Integration tests failed with error: {e}\n")
        all_results.append(("Query Integration", False))
    
    # Final Summary
    print_header("OVERALL TEST SUMMARY")
    
    print("Module Test Results:")
    print("-" * 70)
    for module_name, passed in all_results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {module_name:.<50} {status}")
    
    print("-" * 70)
    
    total = len(all_results)
    passed = sum(1 for _, p in all_results if p)
    failed = total - passed
    
    print(f"\n  Total Modules Tested: {total}")
    print(f"  ✅ Passed: {passed}")
    print(f"  ❌ Failed: {failed}")
    
    if failed == 0:
        print("\n  🎉 ALL QUERY MODULE TESTS PASSED! 🎉")
    else:
        print(f"\n  ⚠️  {failed} module(s) had test failures")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all_query_tests())