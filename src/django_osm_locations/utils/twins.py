"""
A place twice: a point, and the district of its city that has its name.

Deurne is a node of 86,297 people and the admin_level 9 district of
Antwerpen around it; Weesp in Amsterdam, Bunde in Meerssen. In the packs
33,308 places in 41 countries were both -- 13,168 in Germany, 4,402 in
Britain -- and of 1,308 looked at in five of them, 1,306 were a point inside
the district's border. Search found each twice, and the point first: a place
weighs more than a place subarea, and the point has the population.

The point stays. Streets name it as their settlement -- in the Netherlands
and Luxembourg 98% of the twins have streets on them -- and it answers to its
own slug as before. Search leaves it out, and the district answers for both,
ranked by the point's population when it has none of its own.

Used by the indexer, which reads the twins from the database, and by
locations_repair_twins, which finds them in a pack's indexes, so the two
cannot come to different answers.
"""

#: Where search leaves out a point that is a district's twin; the district's slug.
TWIN_OF = 'twin_of'


def name_key(name):
    """A name as the twins are matched on it: case aside."""
    return (name or '').lower()


def innermost_place(entries):
    """The slug of the innermost place among ``entries``, an ancestry, or None."""
    places = [e['slug'] for e in entries or [] if e.get('level') == 'place' and e.get('slug')]
    return places[-1] if places else None


#: The twins among some places or some place subareas, by the same rule:
#: a place with no border, inside the border of a district of the same name
#: in the same city. The last of the place array is the innermost, as in the
#: index's ancestry.
SQL_FOR_TWINS = """
    SELECT p.id, d.id, d.slug, p.population
    FROM "locations_place" p
    JOIN "locations_placesubarea" d
      ON lower(d.name) = lower(p.name)
     AND d.place[array_length(d.place, 1)][2] = p.place[array_length(p.place, 1)][2]
     AND NOT d.is_merged
     AND d.border && p.center AND ST_Intersects(d.border, p.center)
    WHERE p.border IS NULL AND p.center IS NOT NULL AND NOT p.is_merged
      AND {side}.id = ANY(%s)
    ORDER BY p.id, d.id
"""


def twins_of(model_name, ids):
    """
    ``{id: fields}`` for the rows among ``ids`` that are, or have, a twin.

    A place: ``{'twin_of': <district slug>}``. A place subarea with no
    population of its own: ``{'population': <its twin's>}``, the largest
    where it has more than one.
    """
    from django.db import connection

    side = {'place': 'p', 'placesubarea': 'd'}.get(model_name)
    if side is None or not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(SQL_FOR_TWINS.format(side=side), [list(ids)])
        rows = cursor.fetchall()
    found = {}
    for place_id, district_id, district_slug, population in rows:
        if side == 'p':
            # two districts of the name around one point: the first by id, as
            # the repair takes it
            found.setdefault(place_id, {TWIN_OF: district_slug})
        elif population:
            fields = found.setdefault(district_id, {'population': population})
            fields['population'] = max(fields['population'], population)
    return found
