import itertools

from django.db import models

from .base import LocationBase, LocationBorderBase, LocationOSMBorderBase, LocationOSMMultilineBase, \
    LocationMergableBase
from .fields import DenormArrayField


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
        return CountrySubarea.objects.filter(center__within=self.bbox_polygon)

    @property
    def places(self):
        return Place.objects.filter(center__within=self.bbox_polygon)

    @property
    def placesubareas(self):
        return PlaceSubarea.objects.filter(center__within=self.bbox_polygon)

    @property
    def streets(self):
        return Street.objects.filter(center__within=self.bbox_polygon)


class CountrySubarea(LocationMergableBase, LocationOSMBorderBase):
    """
    Model for all subareas of country, greater than towns/villages/places.
    """

    denorm_fields = ('country', 'countrysubarea')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-area', 'relation_osm_id']

    @property
    def subareas(self):
        return PlaceSubarea.objects.filter(center__within=self.bbox_polygon)

    @property
    def places(self):
        return Place.objects.filter(center__within=self.bbox_polygon)

    @property
    def streets(self):
        return Street.objects.filter(center__within=self.bbox_polygon)


class Place(LocationOSMBorderBase):
    """
    Model for all towns/villages/places
    """
    denorm_fields = ('country', 'countrysubarea', 'place')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)
    place = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-area', 'relation_osm_id', 'node_osm_id']

    @property
    def subareas(self):
        return PlaceSubarea.objects.filter(center__within=self.bbox_polygon)

    @property
    def streets(self):
        return Street.objects.filter(center__within=self.bbox_polygon)


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
        ordering = ['-area', 'relation_osm_id']

    @property
    def streets(self):
        return Street.objects.filter(center__within=self.bbox_polygon)


class Street(LocationMergableBase, LocationOSMMultilineBase):

    denorm_fields = ('country', 'countrysubarea', 'place', 'placesubarea')
    country = DenormArrayField(max_length=255)
    countrysubarea = DenormArrayField(max_length=255)
    place = DenormArrayField(max_length=255)
    placesubarea = DenormArrayField(max_length=255)

    class Meta:
        ordering = ['-length', 'relation_osm_id', 'way_osm_id']
