"""
Several Geofabrik extracts read as one.

Some countries do not fit one extract: Spain's Canary Islands are in Africa's.
The importer reads a single file in several passes, so the extracts are merged
into one first.

The merge streams. An extract is sorted -- nodes, then ways, then relations,
each by id -- so the files are walked side by side and the lowest object
written next, the way a merge sort joins two runs. pyosmium's MergeInputReader
does the same job by holding every input in memory, which for Spain is more
than the machine has. An object in both files -- a border node, the country's
own relation -- is written once, in its newest version.
"""
import os

ORDER = {'n': 0, 'w': 1, 'r': 2}


def _key(obj):
    return ORDER[obj.type_str()], obj.id


def merge_extracts(paths, out_path):
    """Merge sorted ``.osm.pbf`` extracts into ``out_path``; return objects written."""
    import osmium

    partial = out_path + '.part'
    if os.path.exists(partial):
        os.remove(partial)
    readers = [iter(osmium.FileProcessor(path)) for path in paths]
    heads = [next(reader, None) for reader in readers]
    # the format said outright: the .part suffix hides it
    writer = osmium.SimpleWriter(osmium.io.File(partial, 'pbf'))
    written = 0
    try:
        while True:
            live = [i for i, head in enumerate(heads) if head is not None]
            if not live:
                break
            lowest = min(_key(heads[i]) for i in live)
            same = [i for i in live if _key(heads[i]) == lowest]
            newest = max(same, key=lambda i: heads[i].version)
            writer.add(heads[newest])
            written += 1
            for i in same:
                heads[i] = next(readers[i], None)
    finally:
        writer.close()
    os.replace(partial, out_path)
    return written


def is_stale(out_path, paths):
    """Whether ``out_path`` is missing or older than any of ``paths``."""
    if not os.path.exists(out_path):
        return True
    built = os.path.getmtime(out_path)
    return any(os.path.getmtime(path) > built for path in paths)
