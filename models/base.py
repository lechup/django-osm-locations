from __future__ import unicode_literals

from django.utils.encoding import python_2_unicode_compatible
from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Polygon
from django.contrib.postgres.fields.array import ArrayField
from django.db import models
from django.utils.functional import cached_property
from django.utils.text import slugify
from unidecode import unidecode

from .managers import MergableDefaultManager, MergableAllManager


@python_2_unicode_compatible
class LocationBase(gis_models.Model):
    """
    Abstract model used for other location models.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, editable=False)
    bbox = ArrayField(
        models.FloatField(),
        size=4,
        default=list,
        help_text='Bouding box - array of 4 floats, first 2 are coordinates of SoutWest point, second 2 are '
                  'cordinates of NortEast point.'
    )

    class Meta:
        abstract = True

    @cached_property
    def slugify(self):
        return slugify(unidecode(self.name))

    @classmethod
    def get_polygon_from_bbox(cls, bbox):
        if len(bbox) == 4:
            bbox_polygon_points = [
                (bbox[0], bbox[1]),
                (bbox[2], bbox[1]),
                (bbox[2], bbox[3]),
                (bbox[0], bbox[3]),
                (bbox[0], bbox[1])
            ]
            return Polygon(bbox_polygon_points)
        else:
            return Polygon()

    @cached_property
    def bbox_polygon(self):
        """
        Return bouding box as a django.contrib.gis.geos.Polygon.
        """

        return self.__class__.get_polygon_from_bbox(self.bbox)

    @cached_property
    def near_bbox(self):
        """
        Return bounding box that is expanded by 50km each way.
        :return: list of 4 floats, [:2] SW point, [3:] NE point or empty list
        """

        delta = 0.41875  # around 50km
        if len(self.bbox) == 4:
            return [
                self.bbox[0] - delta,
                self.bbox[1] - delta,
                self.bbox[2] + delta,
                self.bbox[3] + delta,
            ]
        else:
            return []

    @cached_property
    def near_bbox_polygon(self):
        """
        Return near bouding box as a django.contrib.gis.geos.Polygon.
        """

        return self.__class__.get_polygon_from_bbox(self.near_bbox)

    def _set_slug(self):
        if not self.slug:
            self.slug = self.slugify

    def save(self, *args, **kwargs):
        self._set_slug()
        super(LocationBase, self).save(*args, **kwargs)

    def __str__(self):
        return '{}: {} ({})'.format(
            self.__class__.__name__,
            self.name,
            self.id,
        )


@python_2_unicode_compatible
class LocationOSMBase(gis_models.Model):
    node_osm_id = models.BigIntegerField(default=None, null=True)
    way_osm_id = models.BigIntegerField(default=None, null=True)
    relation_osm_id = models.BigIntegerField(default=None, null=True)

    class Meta:
        abstract = True

    def __str__(self):
        return '{}: {}, {} (Node OSMID: {}, Way OSMID: {}, Relation OSMID: {})'.format(
            self.__class__.__name__,
            self.name,
            self.id,
            self.node_osm_id,
            self.way_osm_id,
            self.relation_osm_id,
        )

    def save(self, *args, **kwargs):
        super(LocationOSMBase, self).save(*args, **kwargs)


class LocationBorderBase(LocationBase):
    center = gis_models.PointField(default=None, null=True, blank=True)
    area = models.FloatField(default=0, blank=True, db_index=True)
    border = gis_models.MultiPolygonField(default=None, null=True, blank=True)
    # duplicated column for border with geography type enabled, some functions works better
    # on geography types, details:
    # https://docs.djangoproject.com/en/1.10/ref/contrib/gis/model-api/#geography-type
    gborder = gis_models.MultiPolygonField(default=None, null=True, blank=True, geography=True)

    def _set_center(self):
        if self.border and not self.center:
            self.center = self.border.point_on_surface

    def _set_area(self):
        if self.gborder and not self.area:
            self.area = self.gborder.area

    def _set_gborder(self):
        if self.border:
            self.gborder = self.border

    @property
    def center_bbox(self):
        """
        Return bounding box calculated as 1km from self.center each way.
        :return: list of 4 floats, [:2] SW point, [3:] NE point or empty list
        """

        delta = 0.009  # around one km in coordinates
        if self.center:
            return [
                self.center[0] - delta,
                self.center[1] - delta,
                self.center[0] + delta,
                self.center[1] + delta,
            ]
        else:
            return []

    def _set_bbox(self):
        # use border to count bounding_box
        if self.border:
            self.bbox = list(self.border.extent)
        # use center to count bouding_box
        elif self.center:
            self.bbox = self.center_bbox

    @cached_property
    def models(self):
        from . import CountrySubarea, Place, PlaceSubarea, Street
        return {
            'countrysubarea': CountrySubarea,
            'place': Place,
            'placesubarea': PlaceSubarea,
            'street': Street,
        }

    @property
    def near_countrysubareas(self):
        return self.models['countrysubarea'].objects.exclude(pk=self.pk).filter(center__within=self.near_bbox_polygon)

    @property
    def near_places(self):
        return self.models['place'].objects.exclude(pk=self.pk).filter(center__within=self.near_bbox_polygon)

    @property
    def near_placesubareas(self):
        return self.models['placesubarea'].objects.filter(center__within=self.near_bbox_polygon)

    @property
    def near_streets(self):
        return self.models['street'].objects.filter(center__within=self.near_bbox_polygon)

    def save(self, *args, **kwargs):
        self._set_gborder()
        self._set_center()
        self._set_area()
        self._set_bbox()
        super(LocationBorderBase, self).save(*args, **kwargs)

    class Meta:
        abstract = True


class LocationOSMBorderBase(LocationBorderBase, LocationOSMBase):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        super(LocationOSMBorderBase, self).save(*args, **kwargs)


class LocationMultilineBase(LocationBase):
    center = gis_models.PointField(default=None, null=True, blank=True)
    length = models.FloatField(default=0, blank=True, db_index=True)
    lines = gis_models.MultiLineStringField(default=None, null=True, blank=True)

    class Meta:
        abstract = True

    def _set_bbox(self):
        if self.lines:
            self.bbox = list(self.lines.extent)

    def save(self, *args, **kwargs):
        self._set_bbox()
        super(LocationMultilineBase, self).save(*args, **kwargs)


class LocationOSMMultilineBase(LocationMultilineBase, LocationOSMBase):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        super(LocationOSMMultilineBase, self).save(*args, **kwargs)


class LocationMergableBase(gis_models.Model):
    is_merged = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Is given location merged with other one?'
    )

    objects = MergableDefaultManager()
    objects_all = MergableAllManager()

    class Meta:
        abstract = True

    def mark_as_merged(self):
        self.is_merged = True
        self.save(update_fields=['is_merged'])

    def mark_as_not_merged(self):
        self.is_merged = False
        self.save(update_fields=['is_merged'])

    def save(self, *args, **kwargs):
        super(LocationMergableBase, self).save(*args, **kwargs)
