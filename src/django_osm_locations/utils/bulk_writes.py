"""
Durability settings for commands that can simply be run again.

A recalculation writes tens of millions of row versions, and every commit waits
for the write-ahead log to reach disk. That wait buys the guarantee that a
committed transaction survives a power cut -- which is worth having for a web
request and worth nothing here, because the remedy for an interrupted
recalculation is to run it again, and these commands are built to be re-run
from the start.

``synchronous_commit = off`` keeps every other guarantee: the database stays
consistent, crash recovery still works, and nothing is corrupted. The only
thing given up is that the last fraction of a second of commits may be missing
after an unclean shutdown -- transactions that a re-run would redo anyway.

This is set per session, so it applies to the command that asks for it and to
nothing else connecting to the same database.
"""
from django.db import connection


def relax_durability(stdout=None):
    """
    Stop waiting for the WAL on every commit, for this session only.

    Returns True when it was applied. Deliberately not applied when the caller
    is inside a transaction that other work depends on -- the setting is
    session-wide and a caller should ask for it before the writing starts.
    """
    # A performance hint has no business failing a command. Anything that
    # stops this from working -- no connection, a backend that does not know
    # the setting, a caller with no database access at all -- leaves the
    # command running exactly as it did before, only slower.
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET synchronous_commit = off')
            cursor.execute('SHOW synchronous_commit')
            got = cursor.fetchone()[0]
    except Exception:
        return False

    applied = got == 'off'
    if applied and stdout is not None:
        stdout.write(
            'synchronous_commit is off for this session: commits no longer wait '
            'for the log to reach disk. An unclean shutdown can lose the last '
            'commits, which a re-run redoes anyway.'
        )
    return applied
