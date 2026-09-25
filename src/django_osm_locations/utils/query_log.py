"""
Keeping Django's query log from eating an import.

With ``DEBUG = True`` Django keeps the last 9000 statements it executed, fully
interpolated, so that ``connection.queries`` can be inspected. That is useful
in a request and ruinous in a bulk import: one ``bulk_create`` of a thousand
streets leaves 0.6MB of SQL behind, measured, and a full deque is 4.9GB.

It is not a hypothetical. A Poland import grew its anonymous memory from 0.02GB
to 3.58GB over its write phases -- steadily, in step with the batches, which is
what made it look like a leak -- until memory pressure on the session got it
stopped 45 minutes in.

Django's own advice is not to run management commands with DEBUG on. This makes
the consequence of ignoring that advice bounded rather than fatal, and says so
once rather than silently.
"""
from collections import deque

from django.conf import settings
from django.db import connections


def stop_logging_queries(stdout=None):
    """
    Discard executed statements instead of accumulating them.

    Replaces each connection's log with a zero-length deque rather than
    clearing it periodically: clearing changes what ``CaptureQueriesContext``
    and ``assertNumQueries`` see partway through, and a test that counts an
    importer's queries has every right to keep working.

    Returns the names of the connections changed, empty when DEBUG is off and
    there is nothing to do.
    """
    if not settings.DEBUG:
        return []

    changed = []
    for alias in connections:
        connection = connections[alias]
        connection.queries_log = deque(maxlen=0)
        changed.append(alias)

    if stdout is not None and changed:
        stdout.write(
            'DEBUG is on, so Django would keep the last 9000 statements it '
            'runs -- 0.6MB each for a batch of streets, 4.9GB for a full '
            'deque. Query logging is off for this command; turn DEBUG off to '
            'make the import faster as well.'
        )
    return changed
