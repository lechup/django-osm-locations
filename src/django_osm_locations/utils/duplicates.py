"""
When two rows are one location, and what the one is.

Two kinds, both found in the Poland pack:

* a street in pieces. One name in one place is one street -- a gap in the
  line is still the same street -- and the import joined the pieces only
  inside a place's border, which a village drawn as a point does not have:
  21,973 names in Poland stood as 64,647 streets, and of a sample of 200
  names, 177 were in places with no border.
* a house twice. 4.7% of the house numbers in Kraków and 6.2% in Łódź came
  more than once on the same street, and the detail endpoint answered them
  with a 409. Asked of OSM, a sample of 120 was a shop, an office or a guest
  house carrying the address of the building it is in (69), several
  buildings under one number (23), an address point and the outline of its
  building (10), two address points (10) -- and one pair of staircase
  entrances.

Used by the recalculation, which resolves them in the database, and by
locations_repair_addresses, which resolves them in a pack's indexes, so the
two cannot come to different answers.
"""
import math
from collections import Counter

#: How far apart one number on one street can be and still be one house. 97%
#: of the repeats in Kraków and Łódź are within it; beyond it they are other
#: houses -- Okólna 76 in Łódź and Okólna 76 in Łagiewniki Nowe, six
#: kilometres up the same road.
SAME_HOUSE_METRES = 100


def metres_between(a, b):
    """Distance between two ``(lon, lat)`` points, flat-earth: good to a metre at house range."""
    across = math.cos(math.radians((a[1] + b[1]) / 2)) * 111320
    return math.hypot((a[0] - b[0]) * across, (a[1] - b[1]) * 110540)


def clusters(points, metres=SAME_HOUSE_METRES):
    """
    ``points`` in groups, as lists of their indexes, in the order given.

    A point joins a group when it is within ``metres`` of any point already
    in it, so a row of three buildings 60 m apart is one house and two
    houses 6 km apart are two.
    """
    parent = list(range(len(points)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(points)):
        for j in range(i):
            if metres_between(points[i], points[j]) <= metres:
                parent[root(i)] = root(j)
    groups = {}
    for i in range(len(points)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values())


def merged_center(points, outlines):
    """
    Where one house of several is: the average of its building outlines'
    centres, or of its points where it has no outline.

    A shop or an office is a point wherever its mapper put it, often at the
    door, and pulls an average off the building; an outline is the building.
    ``outlines`` says, for each of ``points``, whether it came from one.
    """
    chosen = [p for p, outline in zip(points, outlines) if outline] or list(points)
    return (sum(p[0] for p in chosen) / len(chosen), sum(p[1] for p in chosen) / len(chosen))


def keeper(outlines):
    """Which of several rows stands for the house: the first outline, or the first row."""
    return next((i for i, outline in enumerate(outlines) if outline), 0)


def merged_postcode(postcodes):
    """The postcode most of them carry, the first of those on a tie; '' if none does."""
    counted = Counter(p for p in postcodes if p)
    if not counted:
        return ''
    best = max(counted.values())
    return next(p for p in postcodes if p and counted[p] == best)
