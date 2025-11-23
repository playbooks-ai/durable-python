# Test Suite Documentation

## Overview

This directory contains comprehensive tests for the durable-python package. The test suite ensures all functionality works correctly and helps prevent regressions.

## Test Files

- **`test_transformer.py`** - Comprehensive tests for AST transformation logic (68 tests)
- **`test_execution.py`** - Comprehensive tests for execution classes and async state management (48 tests)
- **`__init__.py`** - Test package initialization

**Total:** 116 tests across both modules

## Running Tests

### Run All Tests
```bash
pytest tests/ -v
```

### Run Specific Test File
```bash
pytest tests/test_transformer.py -v
```

### Run Specific Test Class
```bash
pytest tests/test_transformer.py::TestMakeDurable -v
```

### Run Specific Test
```bash
pytest tests/test_transformer.py::TestMakeDurable::test_caching_behavior -v
```

### Run with Detailed Output
```bash
pytest tests/ -vv
```

### Run with Timing Information
```bash
pytest tests/ --durations=10
```

## Test Structure

### test_transformer.py (68 tests)

Tests are organized into 5 main classes:

#### 1. TestMakeDurable
Tests for the `make_durable()` function - the main entry point for transforming async functions.

**Coverage:**
- Basic transformation
- Parameter handling
- Caching behavior
- Exception handling

#### 2. TestDurableTransformer
Tests for the `DurableTransformer` class methods - the core AST transformation logic.

**Coverage:**
- Variable collection (parameters and locals)
- Checkpoint expression building
- Statement wrapping
- Loop transformation
- Conditional transformation
- Await wrapping
- Checkpoint counting

#### 3. TestComplexTransformations
Tests for complex real-world scenarios combining multiple features.

**Coverage:**
- Nested loops
- Nested conditionals
- Multiple awaits
- Try-except blocks
- With statements
- Complex expressions

#### 4. TestEdgeCases
Tests for edge cases and unusual scenarios.

**Coverage:**
- Empty functions
- Functions with docstrings only
- Complex default arguments
- Break/continue statements
- For-else/while-else clauses
- Existing global statements

#### 5. TestIntegration
End-to-end integration tests.

**Coverage:**
- Full transformation pipeline
- AST unparsing validation
- Counter behavior

## Writing New Tests

### Test Template

```python
def test_your_feature(self):
    """Brief description of what this test validates."""
    # 1. Setup - Define or parse test code
    source = """
async def test_func():
    x = 1
"""
    tree = ast.parse(source)
    func_def = tree.body[0]
    
    # 2. Execute - Create transformer and transform
    transformer = DurableTransformer()
    result = transformer.visit_AsyncFunctionDef(func_def)
    
    # 3. Assert - Verify expected behavior
    assert isinstance(result, ast.AsyncFunctionDef)
    # Add more specific assertions
```

### Testing Guidelines

1. **Clear Test Names**: Use descriptive names that explain what's being tested
   - ✅ `test_transform_for_loop_creates_index`
   - ❌ `test_for_loop`

2. **Good Documentation**: Include docstrings explaining the test purpose
   ```python
   def test_feature(self):
       """Test that feature X behaves correctly when Y happens."""
   ```

3. **Arrange-Act-Assert Pattern**:
   - **Arrange**: Set up test data
   - **Act**: Execute the code being tested
   - **Assert**: Verify the results

4. **Test One Thing**: Each test should verify one specific behavior

5. **Independent Tests**: Tests should not depend on each other

6. **Use Fixtures**: Leverage pytest fixtures for common setup
   ```python
   @pytest.fixture
   def sample_function():
       async def func():
           return 1
       return func
   ```

### Common Test Patterns

#### Testing AST Transformation
```python
source = """
async def test_func():
    x = 1
"""
tree = ast.parse(source)
func_def = tree.body[0]

transformer = DurableTransformer()
result = transformer.visit_AsyncFunctionDef(func_def)

assert isinstance(result, ast.AsyncFunctionDef)
```

#### Testing make_durable()
```python
async def sample_func(a, b):
    return a + b

result = make_durable(sample_func)

assert isinstance(result, ast.Module)
func_def = result.body[0]
assert len(func_def.args.args) == 0  # Parameters removed
```

#### Testing Exceptions
```python
async def already_transformed():
    return 1

already_transformed.__durable_transformed__ = True

with pytest.raises(AlreadyDurableException):
    make_durable(already_transformed)
```

#### Testing Statement Wrapping
```python
source = """
async def test_func():
    x = 1
"""
tree = ast.parse(source)
func_def = tree.body[0]
stmt = func_def.body[0]

transformer = DurableTransformer()
result = transformer.visit_statement(stmt)

assert len(result) == 1
assert isinstance(result[0], ast.If)  # Wrapped in checkpoint guard
```

## Test Fixtures

### clear_cache (autouse)
Automatically clears the durable cache before and after each test to ensure test independence.

```python
@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the durable cache before each test."""
    _durable_cache.clear()
    yield
    _durable_cache.clear()
```

## Debugging Tests

### Run Specific Test with Output
```bash
pytest tests/test_transformer.py::TestMakeDurable::test_caching_behavior -vv -s
```

### Show Local Variables on Failure
```bash
pytest tests/test_transformer.py -vv --showlocals
```

### Drop into Debugger on Failure
```bash
pytest tests/test_transformer.py --pdb
```

### Print AST for Debugging
```python
import ast

result = make_durable(my_func)
print(ast.dump(result, indent=2))
print(ast.unparse(result))
```

## Coverage Goals

When adding new features to the transformer, ensure:

1. ✅ Happy path is tested
2. ✅ Edge cases are covered
3. ✅ Error conditions raise appropriate exceptions
4. ✅ Complex scenarios combining features work correctly
5. ✅ Integration tests validate end-to-end behavior

## CI/CD Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    pytest tests/ -v --tb=short
```

## Test Maintenance

### When Adding New Features

1. Add tests to appropriate test class
2. Follow existing test patterns
3. Ensure all tests pass: `pytest tests/test_transformer.py -v`
4. Check for linting errors: Review any IDE warnings

### When Fixing Bugs

1. Add a test that reproduces the bug (should fail)
2. Fix the bug
3. Verify the test now passes
4. Ensure all other tests still pass

### When Refactoring

1. Run tests before refactoring: `pytest tests/ -v`
2. Make changes
3. Run tests after refactoring
4. All tests should still pass (green)

## Test Statistics

Current test metrics for transformer tests:

- **Total Tests:** 68
- **Pass Rate:** 100%
- **Execution Time:** ~0.04s
- **Coverage:** Comprehensive

## Questions or Issues?

If you have questions about the tests or find issues:

1. Check the test documentation (this file)
2. Review existing test examples
3. Check the main documentation
4. Open an issue with specific questions

## Contributing Tests

When contributing new tests:

1. Follow the existing test structure
2. Use clear, descriptive names
3. Include docstrings
4. Ensure tests are independent
5. Run full test suite before submitting
6. Update this documentation if needed

---

**Remember:** Good tests are the foundation of reliable software. Write tests that are clear, focused, and maintainable!

