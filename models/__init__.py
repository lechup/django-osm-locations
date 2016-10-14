from django.db import models

from .base import LocationBase, LocationBorderBase, LocationOSMBorderBase, LocationOSMMultilineBase, \
    LocationMergableBase


class Country(LocationBorderBase):
    iso2 = models.CharField('ISO Aplpha-2 Code', max_length=3)
    iso3 = models.CharField('ISO Aplha-3 Code', max_length=3)
    un = models.SmallIntegerField('United Nations Code')
    population = models.IntegerField(default=None, null=True, blank=True)

    class Meta:
        verbose_name_plural = u'countries'
        unique_together = ('iso2', 'iso3', 'un')
        ordering = ['-area']

    @property
    def subareas(self):
        return CountrySubarea.objects.filter(border__intersects=self.border)

    @property
    def places(self):
        return Place.objects.filter(
            models.Q(border__intersects=self.border) | models.Q(center__intersects=self.border)
        )

    @property
    def streets(self):
        return Street.objects.filter(lines__intersects=self.border)


class CountrySubarea(LocationMergableBase, LocationOSMBorderBase):
    """
    Model for all subareas of country, greater than towns/villages/places.
    """

    class Meta:
        ordering = ['-area', 'relation_osm_id']

    @property
    def subareas(self):
        return PlaceSubarea.objects.filter(border__intersects=self.border)

    @property
    def places(self):
        return Place.objects.filter(
            models.Q(border__intersects=self.border) | models.Q(center__intersects=self.border)
        )

    @property
    def streets(self):
        return Street.objects.filter(lines__intersects=self.border)


class Place(LocationOSMBorderBase):
    """
    Model for all towns/villages/places
    """

    class Meta:
        ordering = ['-area', 'relation_osm_id', 'node_osm_id']

    @property
    def subareas(self):
        return PlaceSubarea.objects.filter(border__intersects=self.border)

    @property
    def streets(self):
        return Street.objects.filter(lines__intersects=self.border)


class PlaceSubarea(LocationMergableBase, LocationOSMBorderBase):
    """
    Model for all subareas of towns/villages/places, greater than street eg. districts
    """

    class Meta:
        ordering = ['-area', 'relation_osm_id']

    @property
    def streets(self):
        return Street.objects.filter(lines__intersects=self.border)


class Street(LocationMergableBase, LocationOSMMultilineBase):
    class Meta:
        ordering = ['-length', 'relation_osm_id', 'way_osm_id']
