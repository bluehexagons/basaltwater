# Makefile for Basaltwater development tasks
.PHONY: check check-fast check-ci test test-verbose browser-test panel-dev coverage help clean compile docs-check brand-check package-check artifact-check

SUITE ?=
TEST ?=
TEST_ARGS ?=
BROWSER_ARGS ?=
PANEL_ARGS ?=
TEST_SELECTION = $(foreach suite,$(SUITE),--suite $(suite)) $(TEST)
FULL_CHECKS = compile docs-check brand-check package-check artifact-check

# Default target
help:
	@echo "Basaltwater development tasks:"
	@echo ""
	@echo "  make check-fast        Run local checks and smoke tests (or selected SUITE/TEST)"
	@echo "  make check             Run all checks and the default test suite"
	@echo "  make check-ci          Run all checks and the full suite once with coverage"
	@echo "  make test              Run all tests (concise output, shows failures only)"
	@echo "  make test-verbose      Run all tests with full verbose output"
	@echo "  make test TEST=name    Run specific test file (e.g., TEST=test_scrub_par2)"
	@echo "  make test SUITE=web    Run a named suite; space-separated suites may be combined"
	@echo "  make compile           Check all Python files compile"
	@echo "  make docs-check        Check CLI documentation and local Markdown links"
	@echo "  make brand-check       Check generated identity assets are current"
	@echo "  make package-check     Check package launcher metadata"
	@echo "  make artifact-check    Build, install, and smoke-test the wheel"
	@echo "  make browser-test      Local intensive Chromium/Firefox checks (never run by CI)"
	@echo "  make panel-dev         Local panels with sample data, simulated actions and reload"
	@echo "  make clean             Remove Python cache files"
	@echo "  make help              Show this help message"
	@echo ""
	@echo "Examples:"
	@echo "  make check-fast              # Quick edit/test loop; no wheel build"
	@echo "  make check-ci                # Run the same checks as branch CI"
	@echo "  make test                    # Concise output, shows summary only"
	@echo "  make test-verbose            # Full test names and all output"
	@echo "  make test TEST=test_scrub_par2"
	@echo "  make test TEST=service_tools/test_storage_ops"
	@echo '  make test SUITE="web network" TEST_ARGS="--durations 20"'
	@echo '  make panel-dev PANEL_ARGS="--surface connect"'
	@echo "  make coverage          Run the default suite with branch coverage"

# Full verification with or without coverage. CI does not repeat the suite.
check: $(FULL_CHECKS) test

check-ci: $(FULL_CHECKS) coverage

# Use smoke tests by default; explicit selections replace that default.
check-fast: compile docs-check package-check
	@python3 run_tests.py $(if $(strip $(TEST_SELECTION)),$(TEST_SELECTION),--suite smoke) $(TEST_ARGS)

docs-check:
	@python3 scripts/check_cli_docs.py
	@python3 scripts/check_doc_links.py

brand-check:
	@python3 scripts/export_brand.py --check

package-check:
	@python3 scripts/check_package_metadata.py

artifact-check:
	@python3 scripts/check_wheel_artifact.py

# Run all tests, or combine explicit modules and domain suites.
test:
	@python3 run_tests.py $(TEST_SELECTION) $(TEST_ARGS)

# Explicit developer-machine qualification. No payload downloads or package
# installation, and deliberately not a dependency of check/check-ci/coverage.
browser-test:
	@python3 scripts/run_browser_tests.py $(BROWSER_ARGS)

panel-dev:
	@python3 scripts/panel_dev.py $(PANEL_ARGS)

# Run the default suite with reproducible branch coverage reporting. The
# optional dev dependency is intentionally separate from the normal check.
coverage:
	python3 -m coverage erase
	python3 -m coverage run --branch --source=. --omit='tests/*,build/*,dist/*,.venv/*,*/__pycache__/*' run_tests.py
	python3 -m coverage report --show-missing

# Run all tests with verbose output
test-verbose:
	@python3 run_tests.py -v $(TEST_SELECTION) $(TEST_ARGS)

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
