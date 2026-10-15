from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Polygon
from django.contrib.postgres.fields.array import ArrayField
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils.functional import cached_property
from ..utils.slugify import slugify

from .managers import MergableManager


class LocationBase(gis_models.Model):
    """
    Abstract model used for other location models.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, editable=False)

    #: Translations of ``name``, keyed by language: {"pl": "Niemcy", "en": "Germany"}.
    #:
    #: OSM carries these as ``name:<lang>`` tags, and coverage runs opposite to
    #: row count: a country relation has 200-270 of them, a voivodeship about
    #: 95, while 7-10% of places and 0.5% of streets have any at all. So the
    #: rows that matter most are the cheapest to carry, and the millions of
    #: streets store an empty dict.
    names = models.JSONField(default=dict, blank=True)
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
        """
        The stored slug.

        Goes through the shared helper rather than calling Django's slugify
        on anyascii output directly: this has to agree exactly with the
        database's slugify(), which builds the search vectors, and with what
        the API normalises a query to. Two Python spellings of the same idea
        is one too many for something that fails silently when they diverge.
        """
        return slugify(self.name)

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

    def near_bbox(self, km=1, delta=0.009):
        """
        Return bounding box calculated as delta from self.center each way,
        delta = 0.009 is ~1km in coordinates values for Poland.

        :return: list of 4 floats, [:2] SW point, [3:] NE point or empty list
        """
        if self.center:
            return [
                self.center[0] - delta * km,
                self.center[1] - delta * km,
                self.center[0] + delta * km,
                self.center[1] + delta * km,
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
            # Transliteration can lengthen a name -- a 255-character name
            # in Cyrillic slugifies longer than the column -- so it is cut.
            self.slug = self.slugify[:self._meta.get_field('slug').max_length]

    @classmethod
    def _save_hooks(cls):
        """
        Names of this class's ``_presave_*`` hooks, resolved once per class.

        This used to run ``dir(self)`` on every save and call ``getattr`` on all
        ~125 resulting names to test callability. That was slow in itself, and
        worse, ``getattr`` *evaluates properties*: every save built
        ``bbox_polygon``, ``center_bbox`` and ``center_bbox_polygon`` -- GEOS
        geometry -- purely as a side effect of looking for method names.

        The set of hooks is fixed per class, so it is computed once and cached.

        Note the name: anything containing ``_presave_`` would match its own
        discovery filter and be invoked as a hook.
        """
        methods = cls.__dict__.get('_save_hooks_cache')
        if methods is None:
            methods = tuple(sorted(
                name for name in dir(cls)
                if '_presave_' in name and callable(getattr(cls, name, None))
            ))
            cls._save_hooks_cache = methods
        return methods

    def _presave(self, *args, **kwargs):
        """
        run all _presave_X methods of current class
        that calculates fields based on other fields
        """
        for save_method in self.__class__._save_hooks():
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


class LocationOSMBase(gis_models.Model):
    node_osm_id = models.BigIntegerField(default=None, null=True, db_index=True)
    way_osm_id = models.BigIntegerField(default=None, null=True, db_index=True)
    relation_osm_id = models.BigIntegerField(default=None, null=True, db_index=True)
    admin_level = models.SmallIntegerField(
        default=None,
        null=True,
        blank=True,
        db_index=True,
        help_text='OSM admin_level this location was imported from. Which model '
                  'a boundary becomes depends on it, and it used to be discarded '
                  'afterwards -- leaving a Danish region, a Polish voivodeship '
                  'and a Polish gmina indistinguishable in one table. Null for '
                  'anything not derived from an administrative boundary, such '
                  'as a place node or a street.',
    )

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
    population = models.IntegerField(default=0, blank=True, db_index=True)

    #: Where the administration sits, which is not always inside what it
    #: administers. Poland's ring counties are the clear case: powiat
    #: krakowski surrounds Krakow and excludes it, so its seat is in the hole.
    #: This used to be written into ``center``, and the denormalisation --
    #: which asks which borders contain a row's centre -- then concluded that
    #: the county is inside the city it encircles.
    seat = gis_models.PointField(default=None, null=True, blank=True)
    seat_name = models.CharField(max_length=255, blank=True, default='')
    #: Kept so the seat can be resolved to its Place later. A foreign key
    #: would force the settlement to be imported before anything naming it,
    #: which is not an order the importer can promise.
    seat_osm_node_id = models.BigIntegerField(default=None, null=True, blank=True, db_index=True)

    def _presave_center(self, *args, **kwargs):
        """
        A point inside the border, always.

        ``center`` is what the denormalisation matches against parent borders
        and what a map puts a marker on, so a point outside the thing it
        marks is wrong for both. Where a border exists it therefore wins over
        a stored centre that has fallen outside it.
        """
        if not self.border:
            return
        if self.center and self.border.contains(self.center):
            return
        self.center = self.border.point_on_surface

    #: Equal-area projection used to measure borders. Whatever is stored must
    #: agree with locations_recalculate_areas_and_lengths, which asks PostGIS
    #: for square metres and divides them down.
    AREA_SRID = 6933

    def _presave_area(self, *args, **kwargs):
        """
        Area in square kilometres.

        ``border.area`` is in the SRID's units -- square degrees for 4326 --
        which is neither a usable figure nor what the recalculate command
        writes, so it is projected first.

        Recomputed whenever there is a border rather than only when ``area``
        is unset. The old guard meant a re-import wrote a new border and left
        the previous area beside it, so the two disagreed until
        locations_recalculate_areas_and_lengths next ran.
        """
        if self.border:
            projected = self.border.clone()
            try:
                projected.transform(self.AREA_SRID)
            except Exception:
                # no GDAL, or a geometry it will not project: leave it to
                # locations_recalculate_areas_and_lengths
                self.area = 0
            else:
                self.area = projected.area / 1_000_000

    def _presave_bbox(self, *args, **kwargs):
        # use border to count bounding_box
        if self.border:
            self.bbox = list(self.border.extent)
        # use center to count bouding_box
        elif self.center:
            self.bbox = self.center_bbox

    def _presave_population(self, *args, **kwargs):
        # ignore self.population None and all strings that are not numeric
        # population: None
        # population: 991 (31.12.2015) - http://www.openstreetmap.org/node/2978192547
        # population: ~900
        try:
            self.population = int(self.population)
        except (ValueError, TypeError):
            self.population = 0

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
    )

    objects = MergableManager()

    class Meta:
        abstract = True

    def mark_as_merged(self):
        self.is_merged = True
        self.save(update_fields=['is_merged'])

    def mark_as_not_merged(self):
        self.is_merged = False
        self.save(update_fields=['is_merged'])
