from django.db import models


class MergeableQuerySet(models.QuerySet):
    def not_merged(self):
        return self.filter(is_merged=False)

    def mark_as_not_merged(self):
        return self.update(is_merged=False)

    def merged(self):
        return self.filter(is_merged=True)

    def mark_as_merged(self, into=None):
        """Merge these into ``into`` -- a row or its id -- and what was merged into them with them."""
        into_id = getattr(into, 'pk', into)
        if into_id is not None:
            self.model.objects.filter(merged_into__in=self.exclude(pk=into_id)).update(
                merged_into_id=into_id)
        return self.update(is_merged=True, merged_into_id=into_id)


class MergableManager(models.Manager):
    def get_queryset(self):
        return MergeableQuerySet(self.model, using=self._db)

    def mark_as_merged(self):
        return self.get_queryset().mark_as_merged()

    def mark_as_not_merged(self):
        return self.get_queryset().mark_as_not_merged()

    def not_merged(self):
        return self.get_queryset().not_merged()

    def merged(self):
        return self.get_queryset().merged()
