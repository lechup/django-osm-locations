"""
When one name in one place is several streets.

One name in one place is one street (see utils.duplicates) -- where the
place is a town. Where it is a municipality of villages, each village has its
own: Østergade in Bagenkop, in Rudkøbing and in Tranekær, 45 km apart, was one
street of Langeland, and Østergade 12 three houses with one slug. The import
joined every piece of a name inside a place's border, before any house said
where it was. Of 40 houses sharing a slug in Denmark 36 were on such a street,
and 19 of 40 in Portugal.

The houses say which village a piece is in: their settlement. Pieces whose
houses name different settlements are different streets, and a piece no
house names goes with the nearest piece one does. A line is never cut: pieces
that touch are one piece, so a road running on from one village into the next
stays one road, and its houses are told apart by their slugs instead (see
utils.shared_slugs).

Used by the import, the recalculation and locations_repair_addresses, so
none of them can come to a different answer.
"""
import math
from collections import Counter

#: Pieces this close are one line, whatever their houses say.
TOUCHING_METRES = 50


def settlement_key(name):
    """A settlement as houses are counted by it: case aside."""
    return (name or '').strip().lower()


def _planar(geometry, latitude):
    """``geometry`` in degrees, as metres east and north: good to a metre at a street's range."""
    from shapely.affinity import scale

    return scale(geometry, xfact=math.cos(math.radians(latitude)) * 111320, yfact=110540,
                 origin=(0, 0))


def pieces(lines, metres=TOUCHING_METRES):
    """``lines``, planar -> ``[[index, ...], ...]``: those within ``metres`` of each other, together."""
    from shapely import STRtree

    parent = list(range(len(lines)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # a long road is hundreds of segments: their neighbours, not every pair
    tree = STRtree(lines)
    for i, j in zip(*tree.query(lines, predicate='dwithin', distance=metres)):
        if root(i) != root(j):
            parent[root(j)] = root(i)
    found = {}
    for i in range(len(lines)):
        found.setdefault(root(i), []).append(i)
    return list(found.values())


def nearest(lines, groups, points):
    """For each of ``points``, ``(lon, lat)``, the index of the group of ``lines`` nearest it."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    latitude = sum(line.centroid.y for line in lines) / len(lines)
    shapes = [unary_union([_planar(lines[i], latitude) for i in group]) for group in groups]
    found = []
    for lon, lat in points:
        point = _planar(Point(lon, lat), latitude)
        found.append(min(range(len(groups)), key=lambda k: (shapes[k].distance(point), k)))
    return found


def streets(lines, houses, metres=TOUCHING_METRES):
    """
    Which of ``lines`` are one street: ``[[index, ...], ...]``, the longest first.

    ``lines``: shapely lines in degrees, the pieces of one name in one place.
    ``houses``: ``(lon, lat, settlement)`` of the houses on them, or naming
    them near them. One street, all of ``lines``, unless the houses name more
    than one settlement.
    """
    from shapely.geometry import Point
    from shapely.ops import unary_union

    if not lines:
        return []
    everything = [list(range(len(lines)))]
    latitude = sum(line.centroid.y for line in lines) / len(lines)
    planar = [_planar(line, latitude) for line in lines]
    parts = pieces(planar, metres)
    if len(parts) < 2:
        return everything
    shapes = [unary_union([planar[i] for i in part]) for part in parts]
    votes = [Counter() for _ in parts]
    for lon, lat, settlement in houses:
        key = settlement_key(settlement)
        if key:
            point = _planar(Point(lon, lat), latitude)
            nearest = min(range(len(parts)), key=lambda k: (shapes[k].distance(point), k))
            votes[nearest][key] += 1
    named = {k: min(v.items(), key=lambda kv: (-kv[1], kv[0]))[0]
             for k, v in enumerate(votes) if v}
    if len(set(named.values())) < 2:
        return everything
    by_settlement = {}
    for k, part in enumerate(parts):
        if k in named:
            settlement = named[k]
        else:
            settlement = named[min(named, key=lambda j: (shapes[j].distance(shapes[k]), j))]
        by_settlement.setdefault(settlement, []).extend(part)
    found = [sorted(group) for group in by_settlement.values()]
    return sorted(found, key=lambda group: (-sum(planar[i].length for i in group), group[0]))


#: The houses naming each street near it, with their settlement: as the
#: recalculation reads a street's settlement from them.
SQL_FOR_HOUSES_NEAR = """
    SELECT st.id, ST_X(a.center), ST_Y(a.center), a.settlement_name
    FROM "locations_street" st
    JOIN "locations_address" a
      ON a.center && ST_Expand(st.lines, %s)
     AND a.street_name = st.name
     AND a.settlement_name <> ''
     AND NOT a.is_merged
     AND ST_DWithin(a.center::geography, st.lines::geography, %s)
    WHERE st.id = ANY(%s) AND st.lines IS NOT NULL
"""


def streets_of(ids, metres, box):
    """
    Which of the street rows ``ids`` -- one name in one place -- are one
    street, by the houses within ``metres`` that name it: ``[[id, ...], ...]``,
    the longest first. ``box``, in degrees, holds ``metres`` all round.
    """
    from django.db import connection
    from shapely import wkb

    ids = sorted(ids)
    with connection.cursor() as cursor:
        cursor.execute(SQL_FOR_HOUSES_NEAR, [box, metres, ids])
        houses = cursor.fetchall()
        if len({settlement_key(row[3]) for row in houses}) < 2:
            return [ids]
        cursor.execute('SELECT id, ST_AsBinary(lines) FROM "locations_street" '
                       'WHERE id = ANY(%s) AND lines IS NOT NULL ORDER BY id', [ids])
        rows = cursor.fetchall()
    found = [row[0] for row in rows]
    lines = [wkb.loads(bytes(row[1])) for row in rows]
    # a house near two pieces counts once, for the nearer: streets() decides
    seen, unique = set(), []
    for _, lon, lat, settlement in houses:
        if (lon, lat, settlement) not in seen:
            seen.add((lon, lat, settlement))
            unique.append((lon, lat, settlement))
    groups = [[found[i] for i in group] for group in streets(lines, unique)]
    # a row with no line goes with the first
    lineless = sorted(set(ids) - set(found))
    if lineless:
        groups[0] = sorted(groups[0] + lineless)
    return groups
