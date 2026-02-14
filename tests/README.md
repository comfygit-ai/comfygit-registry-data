# Tests

Comprehensive test suite for the ComfyGit Registry Data Pipeline with multi-package support and recency scoring.

## Running Tests

```bash
# Run all tests (unit + integration)
uv run pytest tests/ -v

# Run only unit tests
uv run pytest tests/unit/ -v

# Run only integration tests
uv run pytest tests/integration/ -v

# Run specific test file
uv run pytest tests/integration/test_augmentation_edge_cases.py -v

# Run specific test class
uv run pytest tests/integration/test_augmentation_edge_cases.py::TestAugmentationRanking -v

# Run with coverage
uv run pytest tests/ --cov=src --cov-report=term-missing
```

## Test Structure

### Unit Tests (`tests/unit/`) - 18 tests

Fast, isolated tests for individual functions and classes.

**`test_build_global_mappings.py`** (8 tests)
- Core multi-package mapping functionality
- Signature handling and version aggregation
- Empty cache and missing metadata edge cases
- Statistics calculation

**`test_recency_scoring.py`** (10 tests)
- Recency multiplier calculation
- Age-based penalty curves
- Latest version date detection
- Ranking with recency factors

### Integration Tests (`tests/integration/`) - 24 tests

End-to-end tests covering the full pipeline from cache building to Manager augmentation.

**`test_multi_package_pipeline.py`** (12 tests)

**TestMultiPackageRanking** (3 tests)
- Multi-package ranking by popularity
- Zero-stats package handling
- Stable ordering for tied scores

**TestDifferentSignatures** (2 tests)
- Signature differentiation
- Multiple packages per signature variant

**TestVersionAggregation** (1 test)
- Version aggregation within packages

**TestLargeScale** (1 test)
- Stress test with 20 packages

**TestFullPipelineWithAugmentation** (2 tests)
- Full pipeline: registry → mappings → Manager → final
- Synthetic package creation

**TestEdgeCases** (3 tests)
- Empty cache, missing metadata, deprecated versions

**`test_augmentation_edge_cases.py`** (7 tests)

**TestAugmentationRanking** (3 tests)
- Manager packages rank below registry packages with stats
- Multiple Manager packages rank correctly (all have score 0)
- Mixed ranking: registry + multiple Manager packages

**TestAugmentationEdgeCases** (4 tests)
- Manager data with no matching registry URLs
- Empty node lists
- Same URL augments existing package (no duplication)
- Accurate stats tracking

**`test_recency_integration.py`** (5 tests)

**TestRecencyIntegration** (5 tests)
- Real-world scenario: active vs abandoned packages
- Crossover point where recency wins over popularity
- Three packages with different ages
- Recency effects across multiple nodes
- Mixed dated/undated packages

## Test Coverage Summary

**Total Tests:** 42 (18 unit + 24 integration)
**Status:** ✅ All passing (0.06s runtime)

### Coverage Areas

✅ Multi-package ranking and scoring
✅ Recency-based scoring adjustments
✅ Different input signatures
✅ Version aggregation
✅ Large-scale handling (20+ packages)
✅ Full pipeline integration
✅ Manager data augmentation
✅ Synthetic package creation
✅ URL-based package matching
✅ Duplicate prevention
✅ Stats tracking accuracy
✅ Edge cases and error conditions
✅ Empty data handling
✅ Deprecated version filtering

## Key Test Scenarios

### Scenario 1: Multi-Package Ranking with Recency
```python
# Popular but old vs less popular but fresh
old-popular:     10,000 downloads, 500 stars, 3 years old → rank 2
fresh-moderate:  5,000 downloads,  250 stars, 1 month old → rank 1
```

### Scenario 2: Manager Augmentation
```python
# Registry package augmented with Manager data
registry-pkg (5000 downloads, 200 stars)
  → provides "Add" from registry
  → augmented with "Multiply" from Manager

# Result
Add::_      = [registry-pkg (rank 1, from registry)]
Multiply::_ = [registry-pkg (rank 1, from manager)]
```

### Scenario 3: Mixed Registry + Synthetic Packages
```python
# One registry, two Manager-only
registry-pkg:  1000 downloads, 50 stars  → rank 1
synthetic-a:   0 downloads,    0 stars   → rank 2
synthetic-b:   0 downloads,    0 stars   → rank 3

# Registry package ranks first due to stats
```

## Test Fixtures

**Integration test fixtures** (`tests/integration/conftest.py`):
- `temp_cache_file` - Auto-cleaning temporary cache file
- `temp_mappings_file` - Auto-cleaning temporary mappings file
- `temp_manager_file` - Auto-cleaning temporary Manager data file
- `sample_packages` - Factory for creating test package data
- `sample_node` - Factory for creating test node metadata
- `write_cache_helper` - Helper to write cache files
- `write_manager_helper` - Helper to write Manager data files

## Test Design Principles

All tests follow **TDD (Test-Driven Development)**:
1. Tests were written first and failed against old code
2. Production code was implemented to satisfy tests
3. All tests now pass

**Characteristics:**
- Tests use temporary files and clean up automatically
- Integration tests verify end-to-end workflows
- Unit tests verify isolated functionality
- No external dependencies or network calls
- Fast execution (<100ms total)
- Comprehensive edge case coverage

## Example Test Output

```
============================== test session starts ==============================
platform linux -- Python 3.13.3, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/akatzfey/projects/comfygit/comfygit-registry-data
configfile: pyproject.toml
collected 42 items

tests/integration/test_augmentation_edge_cases.py::TestAugmentationRanking::test_manager_node_added_ranks_below_registry_with_higher_stats PASSED
tests/integration/test_augmentation_edge_cases.py::TestAugmentationRanking::test_multiple_manager_packages_for_same_node_rank_correctly PASSED
tests/integration/test_augmentation_edge_cases.py::TestAugmentationRanking::test_registry_and_multiple_manager_packages_mixed_ranking PASSED
...
tests/unit/test_recency_scoring.py::TestRecencyRanking::test_very_popular_old_still_beats_unpopular_new PASSED

============================== 42 passed in 0.06s ==============================
```

## Test Growth

- **Initial:** 8 unit tests
- **After multi-package refactor:** 20 tests (8 unit + 12 integration)
- **After recency scoring:** 32 tests (18 unit + 14 integration)
- **After augmentation edge cases:** 42 tests (18 unit + 24 integration)

Each feature addition came with comprehensive test coverage to ensure correctness and prevent regressions.
