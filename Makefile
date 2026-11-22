# Common tasks for working on django-osm-locations.
#
# Two docker stacks, deliberately separate:
#   test  -- tmpfs PostGIS + small OpenSearch, wiped every run  (ports 55432/59200)
#   local -- persistent volumes, tuned for bulk imports         (ports 55433/59201)
# They use different compose project names, so both can run at once.

# The project's own .venv (made by `make install`), unless a virtualenv is
# already active -- then that one, as before.
VENV        := .venv
PYTHON      ?= $(if $(VIRTUAL_ENV),python,$(VENV)/bin/python)
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
COUNTRY_TARGETS := import import-guarded update merge process country-db
ifneq ($(filter $(firstword $(MAKECMDGOALS)),$(COUNTRY_TARGETS)),)
  COUNTRY_ARGS := $(filter-out $(firstword $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq ($(COUNTRY_ARGS),)
    COUNTRY := $(COUNTRY_ARGS)
    $(eval $(COUNTRY_ARGS):;@:)
  endif
endif
# The same for `make os-index remove delete`: words, not flags, because make
# reads anything starting with a dash as its own option (-r, -m, ...).
ifeq ($(firstword $(MAKECMDGOALS)),os-index)
  OS_INDEX_ARGS := $(filter-out os-index,$(MAKECMDGOALS))
  ifneq ($(OS_INDEX_ARGS),)
    $(eval $(OS_INDEX_ARGS):;@:)
  endif
endif
# And `make load-snapshot us`, `make pack pl-2026-10-02`, `make snapshots pl`:
# which pack, as a word.
ifneq ($(filter $(firstword $(MAKECMDGOALS)),load-snapshot pack snapshots),)
  LOAD_ARGS := $(filter-out load-snapshot pack snapshots,$(MAKECMDGOALS))
  ifneq ($(LOAD_ARGS),)
    $(eval $(LOAD_ARGS):;@:)
  endif
endif
KEEP          ?= 1
ISO           ?=
# systemd-oomd kills on memory pressure across the whole user session, and a
# shell started from an editor shares that editor's cgroup: a France import
# that overran once took VS Code, dbus and pipewire down with it. Run the
# heavy commands in a scope of their own with a cap, so an overrun kills the
# import and nothing else. Needs a user systemd; without one this is empty and
# the commands run as before.
IMPORT_LIMIT  ?= 8G
CAPPED        := $(shell systemctl --user show-environment >/dev/null 2>&1 \
                   && echo systemd-run --user --scope -p MemoryMax=$(IMPORT_LIMIT) --quiet --)

# GeoDjango needs GDAL and GEOS. Without the system packages (gdal-bin,
# libgeos-c1v5) the copies bundled in the pyogrio and shapely wheels serve,
# which `make install` brings in anyway. A path already set wins.
ifeq ($(GDAL_LIBRARY_PATH)$(shell ldconfig -p 2>/dev/null | grep -c 'libgdal\.so'),0)
  WHEELS := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>/dev/null)
  export GDAL_LIBRARY_PATH := $(firstword $(wildcard $(WHEELS)/pyogrio.libs/libgdal*.so*))
  ifeq ($(GEOS_LIBRARY_PATH),)
    export GEOS_LIBRARY_PATH := $(firstword $(wildcard $(WHEELS)/shapely.libs/libgeos_c*.so*))
    export LD_LIBRARY_PATH := $(WHEELS)/shapely.libs$(if $(LD_LIBRARY_PATH),:$(LD_LIBRARY_PATH))
  endif
endif

TEST_COMPOSE  := docker compose -f docker-compose.test.yml
LOCAL_COMPOSE := docker compose -f docker-compose.local.yml
LOCAL_ENV     := DJANGO_SETTINGS_MODULE=local.settings

.DEFAULT_GOAL := help
.PHONY: help install test-up test-down test-reset test test-search test-pbf test-all \
        local-up local-stop local-down local-reset migrate import import-guarded update merge \
        coastline clip \
        snapshot pack load-snapshot snapshots \
        recalculate reindex index-status os-index country-db process demo shell dbshell \
        build check bench clean

## meta

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 } \
		/^## /{ h = $$0; gsub(/^## *-*|-* *$$/, "", h); printf "\n\033[1m%s\033[0m\n", h }' $(MAKEFILE_LIST)

install:  ## Install the package and its development extras (into .venv)
	@$(if $(VIRTUAL_ENV),,test -x $(VENV)/bin/python || python3 -m venv $(VENV))
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

local-stop:  ## Stop the persistent stack's containers; local-up starts the same ones again
	$(LOCAL_COMPOSE) stop

local-down:  ## Remove the persistent stack's containers, keeping its data volumes
	$(LOCAL_COMPOSE) down

local-reset:  ## Stop the persistent stack and DELETE its data
	$(LOCAL_COMPOSE) down -v

## data  (runs against the local stack)

migrate: local-up  ## Apply migrations to the persistent database
	$(LOCAL_ENV) $(PYTHON) -m django migrate

import: local-up  ## Import extracts: make import poland norway
	$(LOCAL_ENV) $(CAPPED) $(PYTHON) -m django locations_import $(COUNTRY)

import-guarded: local-up  ## Import unattended, watching oomd: make import-guarded france
	$(LOCAL_ENV) PYTHON=$(PYTHON) LIMIT=$(IMPORT_LIMIT) ./scripts/guarded-import.sh $(COUNTRY)

update: local-up  ## Fetch OSM diffs and reimport: make update poland
	$(LOCAL_ENV) $(CAPPED) $(PYTHON) -m django locations_update $(COUNTRY)

merge: local-up  ## Merge duplicates only: make merge poland
	$(LOCAL_ENV) $(CAPPED) $(PYTHON) -m django locations_import --merge-only $(COUNTRY)

coastline: local-up  ## Download and load land polygons for border clipping
	@mkdir -p local/osm_data/coastline
	@test -f local/osm_data/coastline/land_polygons.shp || { \
		echo "downloading land-polygons-complete-4326 (about 900MB)..."; \
		curl -sSL -o local/osm_data/coastline/land-polygons.zip \
			https://osmdata.openstreetmap.de/download/land-polygons-complete-4326.zip; \
		unzip -o -j -q local/osm_data/coastline/land-polygons.zip \
			-d local/osm_data/coastline; }
	$(LOCAL_ENV) $(PYTHON) -m django locations_load_land_polygons \
		local/osm_data/coastline/land_polygons.shp

clip: local-up  ## Trim borders back to the coastline (needs `make coastline`)
	$(LOCAL_ENV) $(PYTHON) -m django locations_clip_borders_to_land

snapshot: local-up  ## Package a country's live indexes: make snapshot ISO=pl
	@test -n "$(ISO)" || { echo "set ISO=pl (the country's alpha-2 code), or ISO=all"; exit 1; }
	$(LOCAL_ENV) $(PYTHON) -m django locations_export_snapshot \
		$(if $(filter all,$(ISO)),,$(ISO))

load-snapshot: local-up  ## Restore packs: make load-snapshot pl | us | us-ca-2026-10-18 (undated: newest; us: every part)
	@test -n "$(LOAD_ARGS)" || { echo "make load-snapshot pl -- or a dated name; make snapshots to list them"; exit 1; }
	$(LOCAL_ENV) $(PYTHON) -m django locations_import_snapshot $(LOAD_ARGS)

pack:  ## Turn a snapshot into release assets: make pack pl-2026-09-24
	@test -n "$(LOAD_ARGS)" || { echo "make pack <name>; make snapshots to list them"; exit 1; }
	PYTHON=$(PYTHON) ./scripts/pack-snapshot.sh $(LOAD_ARGS)

snapshots: local-up  ## List loadable snapshots: make snapshots [pl]
	$(LOCAL_ENV) $(PYTHON) -m django locations_import_snapshot --list \
		$(if $(LOAD_ARGS),--in $(LOAD_ARGS),)

recalculate: local-up  ## Recompute slugs, ancestry, areas and lengths
	$(LOCAL_ENV) $(PYTHON) -m django locations_recalculate_all

reindex: local-up  ## Rebuild the search index and swap the aliases
	$(LOCAL_ENV) $(PYTHON) -m django locations_reindex

index-status: local-up  ## Compare database counts with the live index
	$(LOCAL_ENV) $(PYTHON) -m django locations_index_status

os-index: local-up  ## Search indexes: make os-index [list | remove [delete] | rollback|forward [apply]] [KEEP=1] [ISO=pl]
	@case "$(firstword $(OS_INDEX_ARGS))" in \
		""|list) mode=--list ;; \
		remove) case "$(word 2,$(OS_INDEX_ARGS))" in \
			"") mode= ;; \
			delete|rm) mode=--delete ;; \
			*) echo "unknown: $(word 2,$(OS_INDEX_ARGS)); make os-index remove [delete]"; exit 1 ;; \
		esac ;; \
		rollback|forward) case "$(word 2,$(OS_INDEX_ARGS))" in \
			"") mode=--$(firstword $(OS_INDEX_ARGS)) ;; \
			apply) mode="--$(firstword $(OS_INDEX_ARGS)) --apply" ;; \
			*) echo "unknown: $(word 2,$(OS_INDEX_ARGS)); make os-index $(firstword $(OS_INDEX_ARGS)) [apply] ISO=pl"; exit 1 ;; \
		esac ;; \
		*) echo "unknown: $(firstword $(OS_INDEX_ARGS)); make os-index [list | remove [delete] | rollback|forward [apply]]"; exit 1 ;; \
	esac; \
	$(LOCAL_ENV) $(PYTHON) -m django locations_index_cleanup $$mode --keep $(KEEP) \
		$(if $(ISO),--country $(ISO),)

country-db:  ## One country in its own database, import to pack: make country-db germany [KEEP_DB=1]
	@# PostgreSQL only: OpenSearch's 3GB would sit idle through the import,
	@# and the script starts it for the reindex and stops it after the pack
	$(LOCAL_COMPOSE) up -d postgis
	@$(MAKE) --no-print-directory wait-healthy CONTAINERS="locations-local-postgis-1"
	$(LOCAL_ENV) PYTHON=$(PYTHON) ./scripts/country-db.sh $(COUNTRY) $(if $(KEEP_DB),--keep,)

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
