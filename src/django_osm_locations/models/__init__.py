from django.db import models

from .base import LocationBase, LocationBorderBase, LocationOSMBase, \
    LocationOSMBorderBase, LocationOSMMultilineBase, LocationMergableBase
from .fields import DenormArrayField


class Country(LocationBorderBase):
    iso2 = models.CharField('ISO Aplpha-2 Code', max_length=3)
    iso3 = models.CharField('ISO Aplha-3 Code', max_length=3)
    un = models.SmallIntegerField('United Nations Code')

    class Meta:
        verbose_name_plural = 'countries'
        unique_together = ('iso2', 'iso3', 'un')
        ordering = ['-area']

    @property
    def subareas(self):
        return CountrySubarea.objects.filter(center__intersects=self.bbox_polygon)

    @property
    def places(self):
        return Place.objects.filter(center__intersects=self.bbox_polygon)

    @property
    def placesubareas(self):
        return PlaceSubarea.objects.filter(center__intersects=self.bbox_polygon)

    @property
    def streets(self):
        return Street.objects.filter(center__intersects=self.bbox_polygon)


class CountrySubarea(LocationMergableBase, LocationOSMBorderBase):
    """
    Model for all subareas of country, greater than towns/villages/places.
    """

    denorm_fields = ('country', 'countrysubarea')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-area']

    @property
    def subareas(self):
        return PlaceSubarea.objects.filter(center__intersects=self.bbox_polygon)

    @property
    def places(self):
        return Place.objects.filter(center__intersects=self.bbox_polygon)

    @property
    def streets(self):
        return Street.objects.filter(center__intersects=self.bbox_polygon)


class Place(LocationMergableBase, LocationOSMBorderBase):
    """
    Model for all towns/villages/places
    """
    denorm_fields = ('country', 'countrysubarea', 'place')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)
    place = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-area']

    @property
    def subareas(self):
        subareas = PlaceSubarea.objects.filter(center__intersects=self.bbox_polygon)
        none_subareas = PlaceSubarea.objects.none()
        return subareas if self.border else none_subareas

    @property
    def subareas_within_border(self):
        return PlaceSubarea.objects.filter(center__intersects=self.border)

    @property
    def streets(self):
        streets = Street.objects.filter(center__intersects=self.bbox_polygon)
        none_streets = Street.objects.none()
        return streets if self.border else none_streets

    @property
    def streets_within_border(self):
        return Street.objects.filter(center__intersects=self.border)


class PlaceSubarea(LocationMergableBase, LocationOSMBorderBase):
    """
    Model for all subareas of towns/villages/places, greater than street eg. districts
    """

    denorm_fields = ('country', 'countrysubarea', 'place', 'placesubarea')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)
    place = DenormArrayField(max_length=255)
    placesubarea = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-area']

    @property
    def streets(self):
        return Street.objects.filter(center__intersects=self.bbox_polygon)


class Street(LocationMergableBase, LocationOSMMultilineBase):

    denorm_fields = ('country', 'countrysubarea', 'place', 'placesubarea')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)
    place = DenormArrayField(max_length=255)
    placesubarea = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-length']
    @property
    def addresses(self):
        return Address.objects.filter(street__contains=[[self.name, self.slug]])


# No existing base pairs a point with OSM ids: the bordered and multiline
# variants both bring geometry an address does not have.
class Address(LocationMergableBase, LocationBase, LocationOSMBase):
    """
    A house number, and what it is a house number on.

    The first thing here that knows where a street is rather than guessing:
    64% of Poland's addressed features and 80% of Britain's name both their
    street and their settlement, against 0.6% of roads carrying addr:city
    themselves. An address states the association; a nearest-place search
    infers it.

    A point, not a polygon -- an address has no extent worth storing, which
    keeps it out of the border clipping and makes it far cheaper per document
    than a street.

    ``street`` is the fifth denormalised level, and the reason a full parent
    path can be printed: Polska > pomorskie > Gdansk > Lakowa > Lakowa 5.
    """

    house_number = models.CharField(max_length=64, db_index=True)
    #: As tagged, before being matched to a Street row. Kept because the match
    #: is by name, and it helps to see what was matched on.
    street_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    #: addr:city, or addr:place where a settlement has no streets -- 35% of
    #: Poland's addresses against 0.3% of Britain's, which is village
    #: addressing rather than a gap.
    settlement_name = models.CharField(max_length=255, blank=True, default="", db_index=True)
    postcode = models.CharField(max_length=32, blank=True, default="", db_index=True)

    denorm_fields = ('country', 'countrysubarea', 'place', 'placesubarea', 'street')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)
    place = DenormArrayField(max_length=255)
    placesubarea = DenormArrayField(max_length=255)
    street = DenormArrayField(max_length=255)

    class Meta:
        verbose_name_plural = 'addresses'
        ordering = ('street_name', 'house_number')

    @property
    def slugify(self):
        """
        Derived, never disambiguated.

        There are 8.5 million of these for Poland alone, and putting them
        through the duplicate-resolution pass would be both enormous and
        pointless: a street slug is already unique, so a house number on it
        is unique too.
        """
        from ..utils.slugify import slugify

        parent = self._parent_slug()
        if parent:
            return slugify('{}-{}'.format(parent, self.house_number))
        return slugify(self.name)

    def _presave_bbox(self, *args, **kwargs):
        """
        A bbox around the point, since there is nothing else to box.

        LocationBase carries no bbox hook -- the bordered and multiline bases
        each supply their own -- and Address inherits neither, so without this
        the index document shipped an empty bbox for every address.
        """
        if self.center:
            self.bbox = self.center_bbox

    def _parent_slug(self):
        """The street it is on, or the settlement when it has no street."""
        for level in ('street', 'place'):
            for pair in getattr(self, level, None) or []:
                if len(pair) > 1 and pair[1]:
                    return pair[1]
        return ""

