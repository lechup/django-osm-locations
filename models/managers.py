from django.db import models
from django.contrib.gis.db import models as gis_models


class MergeableQuerySet(models.QuerySet):
    def not_merged(self):
        return self.filter(is_merged=False)

    def mark_as_not_merged(self):
        return self.update(is_merged=False)

    def merged(self):
        return self.filter(is_merged=True)

    def mark_as_merged(self):
        return self.update(is_merged=True)


class MergableDefaultManager(gis_models.GeoManager):
    def get_queryset(self):
        return MergeableQuerySet(self.model, using=self._db).not_merged()

    def mark_as_merged(self):
        return self.get_queryset().mark_as_merged()

    def mark_as_not_merged(self):
        return self.get_queryset().mark_as_not_merged()


class MergableAllManager(MergableDefaultManager):
    def get_queryset(self):
        return MergeableQuerySet(self.model, using=self._db)

    def not_merged(self):
        return self.get_queryset().not_merged()

    def merged(self):
        return self.get_queryset().merged()
