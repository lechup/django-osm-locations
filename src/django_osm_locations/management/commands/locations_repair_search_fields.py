"""
Give indexes built before whole words were indexed the fields that hold them.

``name.words``, ``ancestry_names.words``, ``names_all.words`` and
``names_all.exact`` -- see WORDS in the index definitions -- ``names_all``
as one value per translation rather than one string, and ``place_names``,
the places among the ancestors. A field added to a mapping is indexed only
in what is written after it, so every document is written again, in the
cluster, with ``names_all`` rebuilt from ``names`` and ``place_names`` from
``ancestry``, which every document keeps.

Like locations_repair_ancestry this works on the live indexes, so a pack can
be repaired without the database it came from. A document already written
since is left alone, so a run that stopped can be run again.
"""
import time

from django.core.management.base import BaseCommand

from ...search.client import get_client, get_index_prefix
from ...search.indexes import INDEXES

#: The fields whose definitions changed or are new.
FIELDS = ('name', 'names_all', 'ancestry_names', 'place_names')

#: What is indexed only once a document has been written again. Every
#: document has a name, so one without this has not been.
MARKER = 'name.words'

#: Not written again yet: no whole words, or a place among the ancestors and
#: no place_names -- what the indexes repaired before place_names lack.
UNWRITTEN = {'bool': {'should': [
    {'bool': {'must_not': [{'exists': {'field': MARKER}}]}},
    {'bool': {
        'filter': [{'nested': {'path': 'ancestry', 'query': {'term': {'ancestry.level': 'place'}}}}],
        'must_not': [{'exists': {'field': 'place_names'}}],
    }},
], 'minimum_should_match': 1}}

REWRITE = """
def values = new TreeSet();
if (ctx._source.names != null) {
  for (v in ctx._source.names.values()) {
    if (v != null && v != '') { values.add(v); }
  }
}
ctx._source.names_all = values.isEmpty() ? null : new ArrayList(values);
def places = new ArrayList();
if (ctx._source.ancestry != null) {
  for (e in ctx._source.ancestry) {
    if (e.level == 'place' && e.name != null && e.name != '') { places.add(e.name); }
  }
}
ctx._source.place_names = places.isEmpty() ? null : places;
"""


class Command(BaseCommand):
    help = "Add the whole-word and whole-translation fields to the live indexes."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count what would change; write nothing.')

    def handle(self, *args, **options):
        self.client = get_client(timeout=600)
        prefix = get_index_prefix()
        for name, index in INDEXES.items():
            alias = index.alias(prefix)
            if not self.client.indices.exists(index=alias):
                continue
            self.repair(name, alias, index, options['dry_run'])

    def repair(self, name, alias, index, dry_run):
        # before the mapping has them, no document has them either
        unwritten = UNWRITTEN
        pending = self.client.count(index=alias, body={'query': unwritten})['count']
        if dry_run or not pending:
            self.stdout.write('{}: {} to write again'.format(name, pending))
            return
        self.client.indices.put_mapping(index=alias, body={
            'properties': {field: index.properties[field] for field in FIELDS}})
        task = self.client.update_by_query(
            index=alias, body={'query': unwritten,
                               'script': {'source': REWRITE, 'lang': 'painless'}},
            conflicts='proceed', slices='auto', refresh=True,
            wait_for_completion=False)['task']
        # a few documents are done in a moment, a country's addresses in an hour
        wait = 0.5
        while True:
            status = self.client.tasks.get(task_id=task)
            if status.get('completed'):
                break
            time.sleep(wait)
            wait = min(wait * 2, 10)
        response = status.get('response', {})
        if response.get('failures'):
            raise RuntimeError('{}: {}'.format(name, response['failures'][:3]))
        self.client.indices.forcemerge(index=alias, only_expunge_deletes=True)
        self.stdout.write('{}: {} of {} written again'.format(
            name, response.get('updated', 0), pending))
