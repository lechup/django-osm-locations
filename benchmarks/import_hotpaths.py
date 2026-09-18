"""
Micro-benchmarks for the import hot paths.

Not part of the test suite -- these measure, they do not assert:

    DJANGO_SETTINGS_MODULE=tests.settings python benchmarks/import_hotpaths.py

Node-coordinate lookups used to live here too. libosmium owns that index now,
so the remaining Python-side hot path is the per-object save machinery.
"""
import os
import sys
import time

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')
django.setup()

from django_osm_locations.models import Street  # noqa: E402

STREETS = 5_000_000


def timed(fn, repeat):
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) / repeat


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
    print(f'  hooks discovered once per class: {Street._save_hooks()}')


if __name__ == '__main__':
    bench_presave()
