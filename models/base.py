from __future__ import unicode_literals

from django.utils.encoding import python_2_unicode_compatible
from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Polygon
from django.contrib.postgres.fields.array import ArrayField
from django.contrib.postgres.search import SearchVectorField
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
    center = gis_models.PointField(default=None, null=True, blank=True)
    search_vector = SearchVectorField(
        default=None,
        null=True,
        help_text='Used to enable prefix matching filtering on all denormalized fields of given location.'
    )
    search_vector_name = SearchVectorField(
        default=None,
        null=True,
        help_text='Used to enable prefix matching filtering on name of given location.'
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

    def near_bbox(self, delta=0.009):
        """
        Return bounding box calculated as delta from self.center each way,
        delta = 0.009 is ~1km in coordinates values.

        :return: list of 4 floats, [:2] SW point, [3:] NE point or empty list
        """
        if self.center:
            return [
                self.center[0] - delta,
                self.center[1] - delta,
                self.center[0] + delta,
                self.center[1] + delta,
            ]
        else:
            return []

    @property
    def center_bbox(self):
        return self.near_bbox()

    @cached_property
    def center_bbox_polygon(self):
        """
        Return center bouding box as a django.contrib.gis.geos.Polygon.
        """

        return self.__class__.get_polygon_from_bbox(self.center_bbox)

    def _presave_slug(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.slugify

    def _presave(self, *args, **kwargs):
        """
        run all _presave_X methods of current class
        that calculates fields based on other fields
        """
        save_methods = [
            save_method for save_method in dir(self) if '_presave_' in save_method and callable(
                getattr(self, save_method)
            )
        ]
        for save_method in save_methods:
            getattr(self, save_method)(*args, **kwargs)

    def save(self, *args, **kwargs):
        self._presave(*args, **kwargs)
        super(LocationBase, self).save(*args, **kwargs)

    def __str__(self):
        return '{}: {} ({})'.format(
            self.__class__.__name__,
            self.name,
            self.id,
        )


@python_2_unicode_compatible
class LocationOSMBase(gis_models.Model):
    node_osm_id = models.BigIntegerField(default=None, null=True, db_index=True)
    way_osm_id = models.BigIntegerField(default=None, null=True, db_index=True)
    relation_osm_id = models.BigIntegerField(default=None, null=True, db_index=True)

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


class LocationBorderBase(LocationBase):
    area = models.FloatField(default=0, blank=True, db_index=True)
    border = gis_models.MultiPolygonField(default=None, null=True, blank=True)
    # duplicated column for border with geography type enabled, some functions works better
    # on geography types, details:
    # https://docs.djangoproject.com/en/1.10/ref/contrib/gis/model-api/#geography-type
    gborder = gis_models.MultiPolygonField(default=None, null=True, blank=True, geography=True)

    def _presave_center(self, *args, **kwargs):
        if self.border and not self.center:
            self.center = self.border.point_on_surface

    def _presave_area(self, *args, **kwargs):
        if self.gborder and not self.area:
            self.area = self.gborder.area

    def _presave_gborder(self, *args, **kwargs):
        if self.border:
            self.gborder = self.border

    def _presave_bbox(self, *args, **kwargs):
        # use border to count bounding_box
        if self.border:
            self.bbox = list(self.border.extent)
        # use center to count bouding_box
        elif self.center:
            self.bbox = self.center_bbox

    class Meta:
        abstract = True


class LocationOSMBorderBase(LocationBorderBase, LocationOSMBase):
    class Meta:
        abstract = True


class LocationMultilineBase(LocationBase):
    length = models.FloatField(default=0, blank=True, db_index=True)
    lines = gis_models.MultiLineStringField(default=None, null=True, blank=True)

    class Meta:
        abstract = True

    def _presave_length(self, *args, **kwargs):
        if self.lines and not self.length:
            self.length = self.lines.length

    def _presave_center(self, *args, **kwargs):
        if self.lines and not self.center:
            self.center = self.lines.point_on_surface

    def _presave_bbox(self, *args, **kwargs):
        if self.lines:
            self.bbox = list(self.lines.extent)


class LocationOSMMultilineBase(LocationMultilineBase, LocationOSMBase):
    class Meta:
        abstract = True


class LocationMergableBase(gis_models.Model):
    is_merged = models.BooleanField(
        default=False,
        help_text='Is given location merged with other one?',
        # we have partial index defined manually in migrations
        # 'WHERE NOT is_merged' for each model using this base class
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
