from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils.functional import cached_property
from django.utils.text import slugify
from unidecode import unidecode

from .managers import MergableDefaultManager, MergableAllManager


class LocationBase(gis_models.Model):
    """
    Abstract model used for other location models.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, editable=False)

    objects = gis_models.Manager()

    class Meta:
        abstract = True

    @cached_property
    def slugify(self):
        return slugify(unidecode(self.name))

    def _set_slug(self):
        if not self.slug:
            self.slug = self.slugify

    def save(self, *args, **kwargs):
        self._set_slug()
        super(LocationBase, self).save(*args, **kwargs)

    def __unicode__(self):
        return u'{}: {} ({})'.format(
            self.__class__.__name__,
            self.name,
            self.id,
        )


class LocationOSMBase(gis_models.Model):
    node_osm_id = models.BigIntegerField(default=None, null=True)
    way_osm_id = models.BigIntegerField(default=None, null=True)
    relation_osm_id = models.BigIntegerField(default=None, null=True)

    class Meta:
        abstract = True

    def __unicode__(self):
        return u'{}: {}, {} (Node OSMID: {}, Way OSMID: {}, Relation OSMID: {})'.format(
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
    NEAR_BORDER_STEP = 0.01

    center = gis_models.PointField(default=None, null=True, blank=True)
    area = models.FloatField(default=0, blank=True, db_index=True)
    border = gis_models.MultiPolygonField(default=None, null=True, blank=True)
    # duplicated column for border with geography type enabled, some functions works better
    # on geography types, details:
    # https://docs.djangoproject.com/en/1.10/ref/contrib/gis/model-api/#geography-type
    gborder = gis_models.MultiPolygonField(default=None, null=True, blank=True, geography=True)

    objects = gis_models.Manager()

    def _set_center(self):
        if self.border and not self.center:
            self.center = self.border.point_on_surface

    def _set_area(self):
        if self.gborder and not self.area:
            self.area = self.gborder.area

    def _set_gborder(self):
        if self.border:
            self.gborder = self.border

    @cached_property
    def near_border(self):
        return self.border.envelope.buffer(10 * self.NEAR_BORDER_STEP)

    @cached_property
    def models(self):
        from . import CountrySubarea, Place, PlaceSubarea, Street
        return {
            'countrysubarea': CountrySubarea,
            'place': Place,
            'placesubarea': PlaceSubarea,
            'street': Street,
        }

    def nearest(self):
        """
        Return nearest location object instance for current location object that have border defined. Used for
        generating Place.near_place.
        :return: Place instance or None
        """
        nearest = None
        for i in range(0, 10):
            nearest = self.__class__.objects.exclude(border=None).exclude(pk=self.pk).filter(
                border__intersects=self.border.buffer((i + 1) * self.NEAR_BORDER_STEP)
            ).first()
            if nearest is not None:
                break
        return nearest

    @property
    def near_countrysubareas(self):
        return self.models['countrysubarea'].objects.exclude(pk=self.pk).filter(center__within=self.near_border)

    @property
    def near_places(self):
        return self.models['place'].objects.exclude(pk=self.pk).filter(center__within=self.near_border)

    @property
    def near_placesubareas(self):
        return self.models['placesubarea'].objects.filter(center__within=self.near_border)

    @property
    def near_streets(self):
        return self.models['street'].objects.filter(center__within=self.near_border)

    def save(self, *args, **kwargs):
        self._set_gborder()
        self._set_center()
        self._set_area()
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

    objects = gis_models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
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
