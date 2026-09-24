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
from .client import get_client, get_index_prefix
from .indexes import INDEXES

#: Where the cluster is allowed to write snapshots. Has to match `path.repo`
#: in the cluster's own configuration -- there is no way to nominate a
#: directory at call time.
DEFAULT_REPOSITORY_PATH = '/snapshots'

#: Base name of the repositories registered for this package's snapshots.
#: The country is appended, because each country is published on its own.
DEFAULT_REPOSITORY = 'locations'


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
    Restore a snapshot, waiting for it to finish.

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
    client.snapshot.restore(
        repository=repository, snapshot=name, body=body,
        params={'wait_for_completion': 'false'},
    )


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
