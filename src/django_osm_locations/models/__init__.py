from django.db import models

from .base import LocationBorderBase, LocationOSMBorderBase, LocationOSMMultilineBase, \
    LocationMergableBase
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
