from django.contrib.gis.db import models as gis_models
from django.db import models
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

    def _set_slug(self):
        self.slug = slugify(unidecode(self.name))

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
    center = gis_models.PointField(default=None, null=True, blank=True)
    area = gis_models.DecimalField(max_digits=15, decimal_places=6, default=0, blank=True)
    border = gis_models.MultiPolygonField(default=None, null=True, blank=True)

    objects = gis_models.Manager()

    def _set_area(self):
        if self.border:
            self.area = self.border.area

    def _set_center(self):
        if self.border:
            self.center = self.border.centroid

    def save(self, *args, **kwargs):
        self._set_area()
        self._set_center()
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
