SHELL := /bin/bash
PACKAGE_SLUG=quasiqueue
ifdef CI
	PYTHON_PYENV :=
	PYTHON_VERSION := $(shell python --version|cut -d" " -f2)
else
	PYTHON_PYENV := pyenv
	PYTHON_VERSION := $(shell cat .python-version)
endif
PYTHON_SHORT_VERSION := $(shell echo $(PYTHON_VERSION) | grep -o '[0-9].[0-9]*')

ifeq ($(USE_SYSTEM_PYTHON), true)
	PYTHON_PACKAGE_PATH:=$(shell python -c "import sys; print(sys.path[-1])")
	PYTHON := python
	PYTHON_VENV :=
else
	PYTHON_PACKAGE_PATH:=.venv/lib/python$(PYTHON_SHORT_VERSION)/site-packages
	PYTHON := . .venv/bin/activate && python
	PYTHON_VENV := .venv
endif

# Used to confirm that pip has run at least once
PACKAGE_CHECK:=$(PYTHON_PACKAGE_PATH)/piptools
PYTHON_DEPS := $(PACKAGE_CHECK)


.PHONY: all
all: $(PACKAGE_CHECK)

.PHONY: install
install: $(PYTHON_PYENV) $(PYTHON_VENV) pip

.venv:
	python -m venv .venv

.PHONY: pyenv
pyenv:
	pyenv install --skip-existing $(PYTHON_VERSION)

pip: $(PYTHON_VENV)
	$(PYTHON) -m pip install -e .[dev]

$(PACKAGE_CHECK): $(PYTHON_VENV)
	$(PYTHON) -m pip install -e .[dev]

.PHONY: pre-commit
pre-commit:
	pre-commit install

#
# Formatting
#

.PHONY: chores
chores: ruff_fixes black_fixes dapperdata_fixes

.PHONY: ruff_fixes
ruff_fixes:
	$(PYTHON) -m ruff . --fix

.PHONY: black_fixes
black_fixes:
	$(PYTHON) -m ruff format .

.PHONY: dapperdata_fixes
dapperdata_fixes:
	$(PYTHON) -m dapperdata.cli pretty . --no-dry-run


#
# Testing
#

.PHONY: tests
tests: install pytest ruff_check black_check mypy_check dapperdata_check

.PHONY: pytest
pytest:
	$(PYTHON) -m pytest --cov=./${PACKAGE_SLUG} --cov-report=term-missing tests

.PHONY: pytest_loud
pytest_loud:
	$(PYTHON) -m pytest --log-cli-level=DEBUG -log_cli=true --cov=./${PACKAGE_SLUG} --cov-report=term-missing tests

.PHONY: ruff_check
ruff_check:
	$(PYTHON) -m ruff check

.PHONY: black_check
black_check:
	$(PYTHON) -m ruff format . --check

.PHONY: mypy_check
mypy_check:
	$(PYTHON) -m mypy ${PACKAGE_SLUG}

.PHONY: dapperdata_check
dapperdata_check:
	$(PYTHON) -m dapperdata.cli pretty .


#
# Packaging
#

.PHONY: build
build: $(PACKAGE_CHECK)
	$(PYTHON) -m build
