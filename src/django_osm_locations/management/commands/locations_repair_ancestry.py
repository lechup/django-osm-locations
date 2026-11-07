"""
Repair the ancestry of indexes built before the recalculation was fixed.

A level pushed onto itself listed every row as its own ancestor, a smaller
unit could become the ancestor of a larger one around it, and a merged
duplicate was pushed down beside the original: Kazimierz's path read "Kraków >
Stare Miasto > Kazimierz > Stare Miasto > Kazimierz", powiat krakowski's ended
in gmina Zabierzów and itself, and a fifth of Poland's streets carried a
district twice. Every pack built before then has them.

This works on the live indexes, so a pack can be repaired without the
database it came from: restore it, run this, snapshot it again.

* country subareas, places and place subareas -- the levels pushed onto
  themselves -- lose the repeats, themselves, and every ancestor of their own
  level that is not larger than they are. Few documents, done here.
* streets and addresses lose the repeats, in the cluster: millions of
  documents, most of them untouched, so only the changed ones are written.
"""
import time

from django.core.management.base import BaseCommand

from ...search.client import get_client, get_index_prefix
from ...search.indexes import clean_ancestry, flatten_ancestry

#: index name -> the ancestry level its own rows appear at
SAME_LEVEL = {
    'country-subareas': 'countrysubarea',
    'places': 'place',
    'place-subareas': 'placesubarea',
}

DEDUPE = """
if (ctx._source.ancestry == null) { ctx.op = 'noop'; return; }
def seen = new HashSet();
def kept = new ArrayList();
def slugs = new ArrayList();
def names = new ArrayList();
for (e in ctx._source.ancestry) {
  if (seen.add(e.level + '|' + e.slug)) {
    kept.add(e);
    if (e.slug != null && e.slug != '') { slugs.add(e.slug); }
    if (e.name != null && e.name != '') { names.add(e.name); }
  }
}
if (kept.size() == ctx._source.ancestry.size()) { ctx.op = 'noop'; }
else {
  ctx._source.ancestry = kept;
  ctx._source.ancestry_slugs = slugs;
  ctx._source.ancestry_names = names;
}
"""


def repaired(source, level, areas):
    """A document's ancestry fields cleaned, or None when there is nothing to change."""
    entries = source.get('ancestry') or []
    kept = clean_ancestry(entries, level, source.get('slug'))
    own_area = source.get('area') or 0
    kept = [
        entry for entry in kept
        if entry.get('level') != level or (areas.get(entry.get('slug')) or 0) > own_area
    ]
    if kept == entries:
        return None
    kept, slugs, names = flatten_ancestry(kept)
    return {'ancestry': kept, 'ancestry_slugs': slugs, 'ancestry_names': names}


class Command(BaseCommand):
    help = "Remove repeated, circular and inverted ancestry from the live indexes."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count what would change; write nothing.')

    def handle(self, *args, **options):
        self.client = get_client(timeout=600)
        self.prefix = get_index_prefix()
        dry_run = options['dry_run']
        for name, level in SAME_LEVEL.items():
            self.repair_level(name, level, dry_run)
        for name in ('streets', 'addresses'):
            self.dedupe(name, dry_run)
        if not dry_run:
            # the United States' own pack is the country alone, its states
            # the rest
            for name in list(SAME_LEVEL) + ['streets', 'addresses']:
                if self.client.indices.exists(index=self.alias(name)):
                    self.client.indices.forcemerge(
                        index=self.alias(name), only_expunge_deletes=True)
            self.stdout.write('deleted documents expunged')

    def alias(self, name):
        return '{}-{}'.format(self.prefix, name)

    def scan(self, index, fields):
        page = self.client.search(index=index, scroll='5m', size=2000,
                                  body={'_source': fields, 'query': {'match_all': {}}})
        try:
            while page['hits']['hits']:
                yield from page['hits']['hits']
                page = self.client.scroll(scroll_id=page['_scroll_id'], scroll='5m')
        finally:
            self.client.clear_scroll(scroll_id=page['_scroll_id'])

    def repair_level(self, name, level, dry_run):
        from opensearchpy.helpers import bulk

        index = self.alias(name)
        if not self.client.indices.exists(index=index):
            return
        areas = {h['_source'].get('slug'): h['_source'].get('area')
                 for h in self.scan(index, ['slug', 'area'])}
        actions, seen = [], 0
        for hit in self.scan(index, ['slug', 'area', 'ancestry']):
            seen += 1
            fields = repaired(hit['_source'], level, areas)
            if fields:
                actions.append({'_op_type': 'update', '_index': hit['_index'],
                                '_id': hit['_id'], 'doc': fields})
        if actions and not dry_run:
            bulk(self.client, actions, chunk_size=1000, refresh=True)
        self.stdout.write('{}: {} of {} repaired'.format(name, len(actions), seen))

    def dedupe(self, name, dry_run):
        index = self.alias(name)
        if not self.client.indices.exists(index=index):
            return
        if dry_run:
            self.stdout.write('{}: deduplicated in the cluster when not a dry run'.format(name))
            return
        task = self.client.update_by_query(
            index=index, body={'script': {'source': DEDUPE, 'lang': 'painless'}},
            conflicts='proceed', slices='auto', refresh=True,
            wait_for_completion=False)['task']
        while True:
            status = self.client.tasks.get(task_id=task)
            if status.get('completed'):
                break
            time.sleep(10)
        response = status.get('response', {})
        if response.get('failures'):
            raise RuntimeError('{}: {}'.format(name, response['failures'][:3]))
        self.stdout.write('{}: {} of {} repaired'.format(
            name, response.get('updated', 0), response.get('total', 0)))
