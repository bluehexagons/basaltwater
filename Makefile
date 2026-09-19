# Makefile for Basaltwater development tasks
.PHONY: check test test-verbose coverage help clean compile docs-check brand-check package-check artifact-check

# Default target
help:
	@echo "Basaltwater development tasks:"
	@echo ""
	@echo "  make check             Run compile checks and the default test suite"
	@echo "  make test              Run all tests (concise output, shows failures only)"
	@echo "  make test-verbose      Run all tests with full verbose output"
	@echo "  make test TEST=name    Run specific test file (e.g., TEST=test_scrub_par2)"
	@echo "  make compile           Check all Python files compile"
	@echo "  make docs-check        Check documented CLI entry points"
	@echo "  make brand-check       Check generated identity assets are current"
	@echo "  make package-check     Check package launcher metadata"
	@echo "  make artifact-check    Build, install, and smoke-test the wheel"
	@echo "  make clean             Remove Python cache files"
	@echo "  make help              Show this help message"
	@echo ""
	@echo "Examples:"
	@echo "  make check                   # Run the same checks as CI"
	@echo "  make test                    # Concise output, shows summary only"
	@echo "  make test-verbose            # Full test names and all output"
	@echo "  make test TEST=test_scrub_par2"
	@echo "  make test TEST=service_tools/test_storage_ops"
	@echo "  make coverage          Run the default suite with branch coverage"

# Run the same checks as continuous integration.
check: compile docs-check brand-check package-check artifact-check test

docs-check:
	@python3 scripts/check_cli_docs.py

brand-check:
	@python3 scripts/export_brand.py --check

package-check:
	@python3 scripts/check_package_metadata.py

artifact-check:
	@python3 scripts/check_wheel_artifact.py

# Run tests (all or specific if TEST variable is set)
test:
ifdef TEST
	@python3 run_tests.py $(TEST)
else
	@python3 run_tests.py
endif

# Run the default suite with reproducible branch coverage reporting. The
# optional dev dependency is intentionally separate from the normal check.
coverage:
	python3 -m coverage erase
	python3 -m coverage run --branch --source=. --omit='tests/*,build/*,dist/*,.venv/*,*/__pycache__/*' run_tests.py
	python3 -m coverage report --show-missing

# Run all tests with verbose output
test-verbose:
	@python3 run_tests.py -v

# Check all Python files compile
compile:
	@echo "Checking Python compilation..."
	@find . -name "*.py" -not -path "./.git/*" -not -path "*/__pycache__/*" \
		-not -path "./build/*" -not -path "./dist/*" -not -path "./.venv/*" \
		-not -path "*.egg-info/*" -print0 | xargs -0 python3 -m py_compile
	@echo "All Python files compile successfully"

# Clean Python cache files
clean:
	@echo "Cleaning Python cache files..."
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@echo "Clean complete"
