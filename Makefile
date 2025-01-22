# SPDX-License-Identifier: BSD-2-Clause

# Copyright (C) 2026 embedded brains GmbH & Co. KG

PACKAGE = spectestrunner

VENV ?= .venv

EXCLUDED_FILES =
EXCLUDED_FILES += src/spectestrunner/servicegrpc_pb2_grpc.py
EXCLUDED_FILES += src/spectestrunner/servicegrpc_pb2.py
EXCLUDED_FILES += src/spectestrunner/servicegrpc_pb2.pyi

PY_SRC_FILES = $(filter-out $(EXCLUDED_FILES), $(wildcard src/$(PACKAGE)/*.py))

PY_ALL_FILES = $(filter-out $(EXCLUDED_FILES), $(PY_SRC_FILES) $(wildcard tests/*.py))

all: format analyse check

format: $(PY_ALL_FILES) | prepare
	uv run yapf -i --parallel $^

analyse: $(PY_SRC_FILES) | prepare
	uv run flake8 $^
	uv run mypy $^
	uv run pylint --disable=duplicate-code $^

check: | prepare
	true

dist: all
	uv build

devel-publish: all
	test -z "`git status --short`"
	uv version --bump=dev
	uv sync
	git ci -m "feat: Development release `uv version --short`" pyproject.toml uv.lock
	git tag "devel/`uv version --short`"
	git push upstream
	git push upstream --tags

publish: all
	test -z "`git status --short`"
	uv version --bump=stable
	uv sync
	git ci -m "feat: Stable release `uv version --short`" pyproject.toml uv.lock
	git tag "release/`uv version --short`"
	uv version --bump=patch --bump=dev
	uv sync
	git ci -m "feat: Start development `uv version --short`" pyproject.toml uv.lock
	git push upstream
	git push upstream --tags

VENV_MARKER = $(VENV)/uv-sync-marker

prepare: $(VENV_MARKER)

$(VENV_MARKER): uv.lock
	uv sync --all-groups
	touch $@

ifndef CI
uv.lock: pyproject.toml
	uv lock
	touch $@
endif

grpc:
	python -m grpc_tools.protoc -Isrc/spectestrunner --python_out=src/spectestrunner --pyi_out=src/spectestrunner --grpc_python_out=src/spectestrunner src/spectestrunner/servicegrpc.proto
