"""
Publishing an index, and loading a published one.

The expensive part of this package is not the code, it is the thirteen hours
of importing, clipping, recalculating and indexing that stand between a fresh
checkout and a working search. A published index removes all of it: download
the country you want, restore, done.

What travels is an OpenSearch snapshot, not a tarred data directory. The data
directory is a Lucene layout tied to the version that wrote it and is not
something to hand to a stranger; snapshots are the supported route and restore
into a later OpenSearch than they left.

Two things the snapshot API does not do for us, and both would leave a
consumer with a working index they cannot see:

* a restore brings back index names, not aliases, and every query in this
  package goes through an alias
* an index restored beside an existing one must not evict it, because several
  countries under one alias is the whole point
"""
import contextlib
import re

from .client import get_client, get_index_prefix
from .indexes import INDEXES

#: Where the cluster is allowed to write snapshots. Has to match `path.repo`
#: in the cluster's own configuration -- there is no way to nominate a
#: directory at call time.
DEFAULT_REPOSITORY_PATH = '/snapshots'

#: Base name of the repositories registered for this package's snapshots.
#: The country is appended, because each country is published on its own.
DEFAULT_REPOSITORY = 'locations'

#: Seconds the restore request may take to be accepted. Not waiting for the
#: restore itself, only for the cluster to take it on -- which, on a disk still
#: writing out the pack just unpacked, took France longer than the client's 30.
RESTORE_REQUEST_TIMEOUT = 600


def repository_for(country_token, root=DEFAULT_REPOSITORY_PATH,
                   name=DEFAULT_REPOSITORY):
    """
    The (name, directory) one country is published from.

    Each country gets a repository directory of its own, and that is not
    tidiness. A filesystem repository keeps one manifest at its root -- the
    `index-N` file -- naming every snapshot and every index it holds, and the
    shard data underneath is addressed through it. Put Poland and Denmark in
    one repository and there is no subset of that directory that restores
    Poland alone: the manifest a consumer needs also announces a Denmark whose
    files they were not given. Two directories, two self-contained downloads.
    """
    token = country_token or 'all'
    return '{}-{}'.format(name, token), '{}/{}'.format(root.rstrip('/'), token)


def register_repository(client=None, name=DEFAULT_REPOSITORY,
                        location=DEFAULT_REPOSITORY_PATH):
    """
    Point the cluster at the snapshot directory, idempotently.

    Fails loudly when the cluster was started without `path.repo`, because the
    message OpenSearch gives for that is about access being denied and reads
    like a filesystem permission problem.
    """
    client = client or get_client()
    try:
        client.snapshot.create_repository(
            repository=name,
            body={'type': 'fs', 'settings': {'location': location}},
        )
    except Exception as error:
        raise RuntimeError(
            'Could not register the snapshot repository at {}. The cluster '
            'needs path.repo set to that directory before it starts; see '
            'docker-compose.local.yml. Underlying error: {}'.format(location, error)
        )
    return name


def forget_repository(client=None, name=DEFAULT_REPOSITORY):
    """
    Have the cluster forget a repository, leaving its files alone.

    A registered repository is remembered at the generation -- the index-N --
    it last wrote or read. A pack unpacked into that directory since is
    another repository on disk, and read through the old memory it lists
    nothing: every state packed here once loaded as "nothing to load".
    Forgotten, it is read afresh when it is registered again.
    """
    client = client or get_client()
    try:
        client.snapshot.delete_repository(repository=name)
    except Exception as error:
        if getattr(error, 'status_code', None) != 404:
            raise
    return name


def indexes_for(country_token=None, types=None, prefix=None, client=None):
    """
    The concrete indexes behind the aliases, for one country or all of them.

    Reads what the aliases actually point at rather than recomputing names: a
    snapshot should hold what is being served, not what a naming convention
    says ought to be there.
    """
    client = client or get_client()
    prefix = prefix or get_index_prefix()
    selected = types or list(INDEXES)

    found = []
    for name in selected:
        alias = INDEXES[name].alias(prefix)
        if not client.indices.exists_alias(name=alias):
            continue
        for index in client.indices.get_alias(name=alias):
            if country_token and not _belongs_to(index, prefix, name, country_token):
                continue
            found.append(index)
    return sorted(found)


def _belongs_to(index, prefix, type_name, country_token):
    stem = '{}-{}-'.format(prefix, type_name)
    if not index.startswith(stem):
        return False
    return index[len(stem):].startswith('{}-'.format(country_token))


#: States a snapshot or restore stops in.
FINISHED = ('SUCCESS', 'FAILED', 'PARTIAL')

#: Seconds the shards' retention leases are given to pass their last write
#: before the merge goes ahead anyway; they move about every 30.
LEASE_TIMEOUT = 300


def merge(indexes, client=None, interval=3, lease_timeout=LEASE_TIMEOUT):
    """
    Merge each index into one segment, with no deleted document left in it,
    before it is snapshotted.

    A document written again after indexing stays in its old segment, deleted,
    until a merge drops it, and the deleted ones still count in the term
    statistics every score is built from: Croatia's addresses kept 98,663
    after their repairs, and enough moved for "Ulica 5 Karlovac" to fall from
    first to fourth. Merged away, it was first again.

    OpenSearch deletes softly: a merge keeps whatever a shard's retention
    lease still covers, and the lease catches up with the last write only
    about every 30 s, so a merge straight after the last write keeps it. The
    leases are waited for first, and when they do not catch up nothing is
    merged at all. The merge itself runs as a task, asked for once and
    polled, as `create` polls a snapshot, since on a country's addresses it
    outlasts the client's timeout. A shard that failed to merge is reported
    inside a successful answer, so the answer is read, and what is left
    deleted is counted.
    """
    import time

    from opensearchpy.exceptions import OpenSearchException

    client = client or get_client()
    target = ','.join(indexes)
    deadline = time.monotonic() + lease_timeout
    while _leases_behind(client, target):
        # merged now, the result is known: hours on a country's addresses to
        # keep the very documents this is for
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "after {} s the shards' retention leases still cover their last "
                'writes, and a merge would keep them'.format(lease_timeout))
        time.sleep(interval)

    try:
        # a retried request would start a second merge beside the first
        with _asked_once(client):
            task = client.indices.forcemerge(index=target, params={
                'max_num_segments': 1, 'wait_for_completion': 'false'})['task']
        while True:
            status = client.tasks.get(task_id=task)
            if status.get('completed'):
                break
            time.sleep(interval)
    except OpenSearchException as error:
        # a cluster restarted under the merge forgets the task
        raise RuntimeError('the merge could not be followed to its end: {}'.format(error))
    if status.get('error'):
        raise RuntimeError('the merge failed: {}'.format(status['error']))
    shards = (status.get('response') or {}).get('_shards') or {}
    if shards.get('failed') or not shards.get('total'):
        raise RuntimeError('the merge did not reach every shard: {}'.format(shards))

    client.indices.refresh(index=target)
    docs = client.indices.stats(index=target, metric='docs')['_all']['primaries']['docs']
    if docs.get('deleted'):
        raise RuntimeError('{} deleted documents are left after the merge'.format(docs['deleted']))


def _leases_behind(client, target):
    """Whether a shard's retention lease still covers a write: what a merge would keep."""
    stats = client.indices.stats(index=target, params={
        'level': 'shards',
        'filter_path': 'indices.*.shards.*.seq_no.max_seq_no,'
                       'indices.*.shards.*.retention_leases.leases.retaining_seq_no'})
    # filter_path drops what has nothing to show: a copy with no lease has no
    # retention_leases at all, and then nothing is kept for it
    return any(
        lease['retaining_seq_no'] <= copy.get('seq_no', {}).get('max_seq_no', -1)
        for index in stats.get('indices', {}).values()
        for copies in index.get('shards', {}).values()
        for copy in copies
        for lease in copy.get('retention_leases', {}).get('leases', [])
    )


def create(name, indexes, client=None, repository=DEFAULT_REPOSITORY,
           on_progress=None, interval=3):
    """
    Snapshot the given indexes and wait for them, by asking rather than
    holding the connection open.

    `wait_for_completion` keeps one HTTP request open for the whole snapshot,
    which for anything of this size outlives the client's timeout: the client
    then retries, and the retry is refused because a snapshot of that name is
    already running. Nothing says so -- the error reads as a naming conflict.
    Polling costs one request every few seconds and does not care how long the
    work takes.
    """
    client = client or get_client()
    client.snapshot.create(
        repository=repository,
        snapshot=name,
        body={'indices': ','.join(indexes), 'include_global_state': False},
        params={'wait_for_completion': 'false'},
    )
    return wait_for(name, client=client, repository=repository,
                    on_progress=on_progress, interval=interval)


def wait_for(name, client=None, repository=DEFAULT_REPOSITORY, interval=3,
             on_progress=None):
    """
    Block until a snapshot finishes, reporting progress as it goes.

    Returns its final state; a caller that cares about PARTIAL has to look,
    because a partial snapshot is not an error to OpenSearch and would
    otherwise be published as though it were whole.
    """
    import time

    client = client or get_client()
    while True:
        found = client.snapshot.get(repository=repository, snapshot=name)
        snapshot = (found.get('snapshots') or [{}])[0]
        state = snapshot.get('state')
        if on_progress:
            shards = snapshot.get('shards') or {}
            on_progress(state, shards.get('successful', 0), shards.get('total', 0))
        if state in FINISHED:
            return state
        time.sleep(interval)


def restore(name, client=None, repository=DEFAULT_REPOSITORY, rename_to=None):
    """
    Start restoring a snapshot. It returns once the cluster has taken the
    restore on, not when the restore is done: `wait_for_restore` is what
    waits for that, and nothing may be put under an alias before it returns.

    Aliases are deliberately not restored from the snapshot. What the alias
    should point at depends on what else is already loaded, and that is a
    decision for `point_aliases` once the indexes are back.
    """
    client = client or get_client()
    body = {'include_global_state': False, 'include_aliases': False}
    if rename_to:
        body.update(rename_to)
    # Held open, a restore of this size outlives the client timeout in the
    # same way a snapshot does; the recovery API is how you watch it instead.
    with _asked_once(client):
        client.snapshot.restore(
            repository=repository, snapshot=name, body=body,
            params={'wait_for_completion': 'false',
                    'request_timeout': RESTORE_REQUEST_TIMEOUT},
        )


#: Polls in a row with no shard recovering, and the indexes not yet
#: searchable, before a restore is taken to have stalled. Shards waiting their
#: turn behind the cluster's limit on concurrent recoveries are unassigned,
#: but then another one is always initialising.
RESTORE_STALL_POLLS = 12


def wait_for_restore(indexes, client=None, interval=5, on_progress=None,
                     stall_polls=RESTORE_STALL_POLLS):
    """
    Block until every restored index can answer a search.

    The restore request returns as soon as the cluster accepts it; the shards
    are copied back afterwards, Germany's for about half an hour. An index
    under an alias before its primaries are active fails every search through
    that alias -- the API answered 503 for as long -- and a replaced version
    retired at the same moment leaves nothing else to answer. So this is
    waited for before either.

    Searchable is yellow: every primary active. Green would also wait for
    replicas, which a single-node cluster never allocates.

    A shard nobody is recovering does not come back by waiting -- a disk past
    its watermark refuses it -- nor does an index the cluster never created,
    so `stall_polls` polls in a row with nothing initialising stop it, with
    the cluster's own explanation. `on_progress` is called after each poll
    with the active and total shard counts and the bytes recovered so far of
    those known.
    """
    import time

    client = client or get_client()
    target = ','.join(indexes)
    stalled = 0
    while True:
        # an index the cluster has not created yet reads as red, not an error
        health = client.cluster.health(index=target, params={'ignore': (404, 408)})
        status = health.get('status')
        if on_progress:
            active = health.get('active_shards', 0)
            total = active + health.get('initializing_shards', 0) + health.get('unassigned_shards', 0)
            on_progress(active, total, *_recovered_bytes(client, target))
        if status in ('green', 'yellow'):
            return status
        if not health.get('initializing_shards'):
            stalled += 1
            if stalled >= stall_polls:
                raise RuntimeError('the restore stalled with {} shard(s) unassigned and none '
                                   'recovering: {}'.format(health.get('unassigned_shards', 0),
                                                           _why_unassigned(client)))
        else:
            stalled = 0
        time.sleep(interval)


def _recovered_bytes(client, target):
    """(recovered, total) bytes over the shards the cluster has begun recovering."""
    try:
        found = client.indices.recovery(index=target)
    except Exception:
        return 0, 0
    sizes = [shard.get('index', {}).get('size', {})
             for index in found.values() for shard in index.get('shards', [])]
    return (sum(s.get('recovered_in_bytes', 0) for s in sizes),
            sum(s.get('total_in_bytes', 0) for s in sizes))


def _why_unassigned(client):
    """What the cluster says keeps a shard unassigned, as far as it says."""
    try:
        found = client.cluster.allocation_explain()
    except Exception as error:
        return 'no explanation from the cluster ({})'.format(error)
    reasons = [found.get('allocate_explanation') or found.get('unassigned_info', {}).get('reason')]
    for node in found.get('node_allocation_decisions') or []:
        reasons.extend(d.get('explanation') for d in node.get('deciders') or [])
    return '; '.join(r for r in reasons if r) or 'no explanation from the cluster'


@contextlib.contextmanager
def _asked_once(client):
    """
    The client's retry after a timeout switched off for one request.

    A restore that timed out has usually begun: the retry is then refused
    because the indexes it would create already exist, and the run stops on
    what reads as a clash with something loaded before.
    """
    transport = client.transport
    retry, transport.retry_on_timeout = transport.retry_on_timeout, False
    try:
        yield
    finally:
        transport.retry_on_timeout = retry


def point_aliases(indexes, client=None, prefix=None):
    """
    Put restored indexes under their aliases without evicting anything.

    A restore leaves indexes nobody can query: every search here goes through
    an alias, and `include_aliases: false` means none came back. Adding rather
    than replacing is what lets a second country join a first -- and what
    stops loading Denmark from hiding Poland.
    """
    client = client or get_client()
    prefix = prefix or get_index_prefix()

    actions = []
    for index in indexes:
        alias = _alias_for(index, prefix)
        if alias:
            actions.append({'add': {'index': index, 'alias': alias}})
    if actions:
        client.indices.update_aliases(body={'actions': actions})
    return [action['add']['alias'] for action in actions]


def _alias_for(index, prefix):
    """
    The alias an index belongs under, from its name.

    Longest type name first: `place-subareas` also starts with `places` would
    be wrong, but `places` is a prefix of nothing else here -- sorting by
    length removes the question rather than relying on it.
    """
    for name in sorted(INDEXES, key=len, reverse=True):
        if index.startswith('{}-{}-'.format(prefix, name)):
            return INDEXES[name].alias(prefix)
    return None


STAMP = re.compile(r'^\d{14}$')


def retire_older(indexes, client=None, prefix=None):
    """
    Take older versions of the given indexes out of their aliases and delete
    them: the same part -- a country, or a state, us-ca -- loaded again from a
    newer pack.

    Matched on the whole token: replacing us-ca leaves us-tx alone, and a
    country token never matches a state's.
    """
    client = client or get_client()
    prefix = prefix or get_index_prefix()
    new = set(indexes)
    retired = []
    for index in indexes:
        alias = _alias_for(index, prefix)
        if not alias or not client.indices.exists_alias(name=alias):
            continue
        stem = '{}-'.format(alias)
        token = index[len(stem):].rsplit('-', 1)[0]
        for other in client.indices.get_alias(name=alias):
            if other in new or not other.startswith(stem + token + '-'):
                continue
            if not STAMP.match(other[len(stem + token + '-'):]):
                continue
            client.indices.update_aliases(body={'actions': [
                {'remove': {'index': other, 'alias': alias}}]})
            client.indices.delete(index=other)
            retired.append(other)
    return retired
