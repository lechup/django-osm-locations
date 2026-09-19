# Common tasks for working on django-osm-locations.
#
# Two docker stacks, deliberately separate:
#   test  -- tmpfs PostGIS + small OpenSearch, wiped every run  (ports 55432/59200)
#   local -- persistent volumes, tuned for bulk imports         (ports 55433/59201)
# They use different compose project names, so both can run at once.

PYTHON      ?= python
COUNTRY     ?= poland
PBF         ?=

# `make import poland norway` reads better than COUNTRY="poland norway", but
# make treats bare words as goals to build, not as arguments. So when the first
# goal is one of the country commands, everything after it is taken as its
# argument list and given an empty rule so make stops looking for targets by
# those names.
#
# Scoped to these targets on purpose: doing it for every goal would break
# ordinary invocations like `make clean build`, where "build" is a real target
# and not an argument.
COUNTRY_TARGETS := import update merge process
ifneq ($(filter $(firstword $(MAKECMDGOALS)),$(COUNTRY_TARGETS)),)
  COUNTRY_ARGS := $(filter-out $(firstword $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq ($(COUNTRY_ARGS),)
    COUNTRY := $(COUNTRY_ARGS)
    $(eval $(COUNTRY_ARGS):;@:)
  endif
endif
TEST_COMPOSE  := docker compose -f docker-compose.test.yml
LOCAL_COMPOSE := docker compose -f docker-compose.local.yml
LOCAL_ENV     := DJANGO_SETTINGS_MODULE=local.settings

.DEFAULT_GOAL := help
.PHONY: help install test-up test-down test-reset test test-search test-pbf test-all \
        local-up local-down local-reset migrate import update merge \
        recalculate reindex index-status index-cleanup process demo shell dbshell \
        build check bench clean

## meta

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 } \
		/^## /{ h = $$0; gsub(/^## *-*|-* *$$/, "", h); printf "\n\033[1m%s\033[0m\n", h }' $(MAKEFILE_LIST)

install:  ## Install the package and its development extras
	$(PYTHON) -m pip install -e '.[dev]'

## test stack  (tmpfs, wiped every run)

test-up:  ## Start the test stack and wait for it to be ready
	$(TEST_COMPOSE) up -d
	@$(MAKE) --no-print-directory wait-healthy CONTAINERS="locations-postgis-1 locations-opensearch-1"

test-down:  ## Stop the test stack, keeping nothing (it is tmpfs anyway)
	$(TEST_COMPOSE) down -v

test-reset: test-down test-up  ## Recreate the test stack from scratch

test: test-up  ## Run the unit and database tests
	$(PYTHON) -m pytest

test-search: test-up  ## Run the OpenSearch integration tests
	$(PYTHON) -m pytest -m search

test-pbf:  ## Run the .pbf import test (needs PBF=/path/to/extract.osm.pbf)
	@test -n "$(PBF)" || { echo "set PBF=/path/to/extract.osm.pbf"; exit 1; }
	@test -f "$(PBF)" || { echo "no such file: $(PBF)"; exit 1; }
	@$(MAKE) --no-print-directory test-up
	LOCATIONS_TEST_PBF=$(PBF) $(PYTHON) -m pytest -m pbf

test-all: test test-search  ## Everything that does not need a .pbf extract

## local stack  (persistent, for real imports)

local-up:  ## Start the persistent stack for real imports
	$(LOCAL_COMPOSE) up -d
	@$(MAKE) --no-print-directory wait-healthy CONTAINERS="locations-local-postgis-1 locations-local-opensearch-1"

local-down:  ## Stop the persistent stack, keeping its data
	$(LOCAL_COMPOSE) down

local-reset:  ## Stop the persistent stack and DELETE its data
	$(LOCAL_COMPOSE) down -v

## data  (runs against the local stack)

migrate: local-up  ## Apply migrations to the persistent database
	$(LOCAL_ENV) $(PYTHON) -m django migrate

import: local-up  ## Import extracts: make import poland norway
	$(LOCAL_ENV) $(PYTHON) -m django locations_import $(COUNTRY)

update: local-up  ## Fetch OSM diffs and reimport: make update poland
	$(LOCAL_ENV) $(PYTHON) -m django locations_update $(COUNTRY)

merge: local-up  ## Merge duplicates only: make merge poland
	$(LOCAL_ENV) $(PYTHON) -m django locations_import --merge-only $(COUNTRY)

recalculate: local-up  ## Recompute slugs, ancestry, areas and lengths
	$(LOCAL_ENV) $(PYTHON) -m django locations_recalculate_all

reindex: local-up  ## Rebuild the search index and swap the aliases
	$(LOCAL_ENV) $(PYTHON) -m django locations_reindex

index-status: local-up  ## Compare database counts with the live index
	$(LOCAL_ENV) $(PYTHON) -m django locations_index_status

index-cleanup: local-up  ## Drop superseded indexes
	$(LOCAL_ENV) $(PYTHON) -m django locations_index_cleanup

process: migrate import recalculate reindex  ## Full pipeline: make process poland
	@echo "$(COUNTRY): imported, recalculated and indexed"

## development

demo: local-up  ## Serve the Leaflet demo at http://127.0.0.1:8000/
	$(LOCAL_ENV) $(PYTHON) -m django runserver

shell: local-up  ## Django shell against the persistent database
	$(LOCAL_ENV) $(PYTHON) -m django shell

dbshell: local-up  ## psql against the persistent database
	$(LOCAL_ENV) $(PYTHON) -m django dbshell

bench:  ## Run the import hot-path benchmarks
	DJANGO_SETTINGS_MODULE=tests.settings $(PYTHON) benchmarks/import_hotpaths.py

build: clean  ## Build the wheel and sdist
	$(PYTHON) -m build

check: build  ## Verify the built distributions
	$(PYTHON) -m twine check dist/*

clean:  ## Remove build artefacts and caches
	rm -rf build dist src/*.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -not -path './.git/*' -exec rm -rf {} +



wait-healthy:
	@for c in $(CONTAINERS); do \
		printf 'waiting for %s' "$$c"; \
		for i in $$(seq 1 60); do \
			status=$$(docker inspect --format '{{.State.Health.Status}}' "$$c" 2>/dev/null); \
			if [ "$$status" = "healthy" ]; then printf ' ready\n'; break; fi; \
			if [ "$$i" = "60" ]; then printf ' TIMED OUT\n'; exit 1; fi; \
			printf '.'; sleep 2; \
		done; \
	done
