"""
Micro-benchmarks for the import hot paths.

Not part of the test suite -- these measure, they do not assert. Run with:

    python benchmarks/import_hotpaths.py

against a settings module that can import the models, e.g.

    DJANGO_SETTINGS_MODULE=tests.settings python benchmarks/import_hotpaths.py
"""
import os
import sys
import time

import django

# run from anywhere: the settings module lives at the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')
django.setup()

from django_osm_locations.management.commands.caches.base import CoordsStore  # noqa: E402
from django_osm_locations.models import Street  # noqa: E402

NODES = 300_000
WAY_REFS = 250_000_000     # a country extract easily reaches this
STREETS = 5_000_000


def timed(fn, repeat):
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) / repeat


def bench_coordinate_lookups():
    store = CoordsStore()
    for i in range(NODES):
        store.add(11_000_000_000 + i * 3, i * 1e-6, i * 2e-6)
    probe = [11_000_000_000 + i * 3 for i in range(0, NODES, 7)]
    store.get(probe[0])   # freeze

    # what the old code did: a dict, probed twice per ref (exists then get)
    as_dict = {11_000_000_000 + i * 3: ({}, (i * 1e-6, i * 2e-6)) for i in range(NODES)}

    def dict_pass():
        for p in probe:
            if p in as_dict:
                as_dict[p]

    old = timed(dict_pass, len(probe))
    per_call = timed(lambda: [store.get(p) for p in probe], len(probe))
    batched = timed(lambda: store.get_many(probe), len(probe))
    membership = timed(lambda: store.contains_all(probe), len(probe))

    print('coordinate lookups')
    for label, value in [
        ('dict, exists+get (pre-packing)', old),
        ('CoordsStore.get() per ref', per_call),
        ('CoordsStore.get_many()', batched),
        ('CoordsStore.contains_all()', membership),
    ]:
        print(f'  {label:32} {value * 1e6:7.3f} us/ref'
              f'   -> {value * WAY_REFS / 60:6.1f} min @ {WAY_REFS / 1e6:.0f}M refs')
    print(f'  get_many vs per-ref get: {per_call / batched:.1f}x   '
          f'contains_all vs per-ref: {per_call / membership:.1f}x\n')


def bench_presave():
    n = 20_000

    def run():
        for _ in range(n):
            Street(name='Floriańska')._presave()

    Street(name='warm')._presave()
    per_object = timed(run, n)
    print('_presave dispatch')
    print(f'  {per_object * 1e6:7.2f} us/object -> {per_object * STREETS:6.1f} s '
          f'@ {STREETS / 1e6:.0f}M streets')
    print(f'  hooks discovered once per class: {Street._save_hooks()}\n')


if __name__ == '__main__':
    bench_coordinate_lookups()
    bench_presave()
