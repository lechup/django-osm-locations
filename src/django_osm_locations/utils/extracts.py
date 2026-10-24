"""
Several Geofabrik extracts read as one, and one read in part.

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


def cut_extract(path, bbox, out_path):
    """
    The corner of an extract a small country sits in, written to ``out_path``.

    San Marino and the Vatican have no extract of their own, and reading
    Italy's whole means indexing every Italian node: 11GB of page cache, which
    took the desktop's memory pressure past the point systemd-oomd kills the
    session at. Their corner is a few thousand nodes.

    Kept: the nodes inside ``bbox`` -- (min lon, min lat, max lon, max lat) --
    every way touching one, whole, with the nodes outside that it needs, and
    every relation with a member among those. A relation reaching far past the
    corner, Italy's own border, comes through with members missing; the
    importer already skips a relation it cannot assemble, and the country's
    border, inside the corner, is complete. Four passes, little memory.
    """
    import osmium

    west, south, east, north = bbox
    inside = set()
    for node in osmium.FileProcessor(path, osmium.osm.NODE):
        location = node.location
        if west <= location.lon <= east and south <= location.lat <= north:
            inside.add(node.id)

    ways, needed = set(), set(inside)
    for way in osmium.FileProcessor(path, osmium.osm.WAY):
        refs = [node.ref for node in way.nodes]
        if any(ref in inside for ref in refs):
            ways.add(way.id)
            needed.update(refs)

    relations = set()
    for relation in osmium.FileProcessor(path, osmium.osm.RELATION):
        for member in relation.members:
            if (member.type == 'w' and member.ref in ways) or (
                    member.type == 'n' and member.ref in inside):
                relations.add(relation.id)
                break

    partial = out_path + '.part'
    if os.path.exists(partial):
        os.remove(partial)
    writer = osmium.SimpleWriter(osmium.io.File(partial, 'pbf'))
    written = 0
    try:
        for kind, ids in ((osmium.osm.NODE, needed), (osmium.osm.WAY, ways),
                          (osmium.osm.RELATION, relations)):
            if not ids:
                continue
            for obj in osmium.FileProcessor(path, kind).with_filter(osmium.filter.IdFilter(ids)):
                writer.add(obj)
                written += 1
    finally:
        writer.close()
    os.replace(partial, out_path)
    return written


def relations_extract(path, out_path, keep, fill_from=()):
    """
    The relations ``keep(tags)`` accepts, with their ways and nodes, written
    to ``out_path``; and the member ways no extract had, by id.

    What Geofabrik's state extracts lack: Wyoming's holds 230 of the 299 ways
    of Wyoming's own border, the ones on the state line being cut away, and a
    county on the line shares those ways. The whole country's extract has
    them all, and is too big to import, but not to read three times for the
    borders alone -- a few thousand ways, merged into each state before it is
    imported.

    Nearly all: Alaska's border leaves the country's extract for 150 m of the
    Arctic coast at the Yukon line, one way, and without it the mainland's
    ring never closes -- Alaska imported as a few Aleutian islands. A way the
    country's extract lacks is looked for in ``fill_from``, the neighbours'
    extracts, with its nodes.
    """
    import osmium

    relations, ways = set(), set()
    for relation in osmium.FileProcessor(path, osmium.osm.RELATION):
        if keep({tag.k: tag.v for tag in relation.tags}):
            relations.add(relation.id)
            ways.update(m.ref for m in relation.members if m.type == 'w')

    nodes, found = set(), set()
    if ways:
        for way in osmium.FileProcessor(path, osmium.osm.WAY).with_filter(
                osmium.filter.IdFilter(ways)):
            found.add(way.id)
            nodes.update(node.ref for node in way.nodes)

    missing = ways - found
    fills = []
    for number, extra in enumerate(fill_from):
        if not missing:
            break
        fill = '{}.fill{}.osm.pbf'.format(out_path, number)
        missing -= _ways_extract(extra, fill, missing)
        fills.append(fill)

    main = out_path + ('.main.osm.pbf' if fills else '.part')
    if os.path.exists(main):
        os.remove(main)
    writer = osmium.SimpleWriter(osmium.io.File(main, 'pbf'))
    written = 0
    try:
        for kind, ids in ((osmium.osm.NODE, nodes), (osmium.osm.WAY, found),
                          (osmium.osm.RELATION, relations)):
            if not ids:
                continue
            for obj in osmium.FileProcessor(path, kind).with_filter(osmium.filter.IdFilter(ids)):
                writer.add(obj)
                written += 1
    finally:
        writer.close()
    if fills:
        # a shared node -- where the filled way meets the rest -- is written once
        written = merge_extracts([main] + fills, out_path)
        for part in [main] + fills:
            os.remove(part)
    else:
        os.replace(main, out_path)
    return written, sorted(missing)


def _ways_extract(path, out_path, ids):
    """The ways of ``ids`` that ``path`` has, with their nodes, to ``out_path``; their ids."""
    import osmium

    found, nodes = set(), set()
    for way in osmium.FileProcessor(path, osmium.osm.WAY).with_filter(osmium.filter.IdFilter(ids)):
        found.add(way.id)
        nodes.update(node.ref for node in way.nodes)
    writer = osmium.SimpleWriter(osmium.io.File(out_path, 'pbf'))
    try:
        for kind, wanted in ((osmium.osm.NODE, nodes), (osmium.osm.WAY, found)):
            if not wanted:
                continue
            for obj in osmium.FileProcessor(path, kind).with_filter(osmium.filter.IdFilter(wanted)):
                writer.add(obj)
    finally:
        writer.close()
    return found


def is_stale(out_path, paths):
    """Whether ``out_path`` is missing or older than any of ``paths``."""
    if not os.path.exists(out_path):
        return True
    built = os.path.getmtime(out_path)
    return any(os.path.getmtime(path) > built for path in paths)
