"""
When several houses have one slug.

An address's slug is its street's and its number -- ``lakowa-5`` -- or its
place's and its number when it is on no street, and was taken to be unique
by construction. In the packs 8.0 million addresses, 6.5%, shared one, and
their detail answered 409. Three kinds, in a sample of 160 from Utah,
Portugal, Denmark and Austria:

* a house naming a street it was not linked to has its place's slug:
  ``kearns-5935`` was seven houses on three streets, and in Utah 69% of the
  addresses shared a slug that way
* one street of a name for a municipality of villages, each with its own
  (see utils.settlements), and Østergade 12 in each
* a village numbered house by house in each of its hamlets, with no streets:
  Furth an der Triesting 46, seven kilometres from Furth an der Triesting 46

Each house sharing a slug is told apart by what it has and the others may
not, first to last: the street it names, its settlement, its district, the
nearest place within its own, and its OSM id, which no other has. The
qualifier goes after the number -- ``kearns-5935-south-dewdrops-drive`` --
and the slug they shared stays on each as ``shared_slug``, where the detail
endpoint finds them all and answers 409 as it did.

Used by the recalculation and by locations_repair_slugs, so the two cannot
come to different answers.
"""
from .slugify import slugify

#: The slug the houses shared, on each of them.
SHARED_SLUG = 'shared_slug'

#: The longest a slug can be, as the column holds it.
SLUG_LENGTH = 255

#: How far the nearest place within a house's own can be and still name it.
NEAREST_PLACE_METRES = 5000


def qualified(base_slug, qualifier):
    """
    base-qualifier, within the column: the base gives way, not the qualifier,
    since the qualifier is what makes it unique. As streets are qualified.
    """
    suffix = '-{}'.format(qualifier)[:SLUG_LENGTH // 2]
    return base_slug[:SLUG_LENGTH - len(suffix)] + suffix


def qualifiers(house):
    """
    What may tell ``house`` apart, best first, as slugs.

    ``house``: a dict with ``linked`` (on a street), ``street_name``,
    ``settlement``, ``district`` and ``nearest`` (the names), and ``osm_id``.
    The street it names only when it is on none: on a street, every house
    of the slug names that one.
    """
    names = [] if house.get('linked') else [house.get('street_name')]
    names += [house.get('settlement'), house.get('district'), house.get('nearest')]
    for name in names:
        value = slugify(name or '')
        if value:
            yield value
    yield str(house['osm_id'])


def qualify(base_slug, houses, taken):
    """
    ``[slug, ...]``: one for each of ``houses``, in their order, none in ``taken``.

    ``taken``: every slug already in use that starts with ``base_slug`` and a
    hyphen; the slugs given out are added to it. A qualifier the base already
    has adds nothing -- Furth an der Triesting 46 in Furth an der Triesting
    -- and is passed over.
    """
    slugs = []
    for house in houses:
        for value in qualifiers(house):
            if '-{}-'.format(value) in '-{}-'.format(base_slug):
                continue
            candidate = qualified(base_slug, value)
            if candidate not in taken:
                break
        else:
            candidate = qualified(base_slug, '{}-{}'.format(house['osm_id'], len(slugs)))
        taken.add(candidate)
        slugs.append(candidate)
    return slugs
