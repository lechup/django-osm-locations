from django_filters.constants import EMPTY_VALUES
import django_filters
from django.contrib.postgres.search import SearchQuery

from ..utils.slugify import slugify
from ..models import Country, CountrySubarea, Place, PlaceSubarea, Street


class CenterWithinLocationSlugFilter(django_filters.CharFilter):
    def __init__(self, *args, **kwargs):
        self.location_model = kwargs.pop('location_model')
        super(CenterWithinLocationSlugFilter, self).__init__(*args, **kwargs)

    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs
        try:
            location_obj = self.location_model.objects.get(slug=value)
        except self.location_model.DoesNotExist:
            return qs
        else:
            return qs.filter(
                center__within=location_obj.border
            )


class SearchQueryToTsquery(SearchQuery):
    """
    Use to_tsquery instead of plainto_tsquery to support prefix matching

    eq.g 'tr:*' will match tree word
    """
    def as_sql(self, compiler, connection):
        params = [self.value]
        if self.config:
            config_sql, config_params = compiler.compile(self.config)
            template = 'to_tsquery({}::regconfig, %s)'.format(config_sql)
            params = config_params + [self.value]
        else:
            template = 'to_tsquery(%s)'
        if self.invert:
            template = '!!({})'.format(template)
        return template, params


class PrefixSearchFilter(django_filters.CharFilter):
    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs
        search_vectors = value.lower().replace(',', ' ').split(' ')
        search_query = None
        for search_vector in search_vectors:
            if search_vector:
                # normalize to slug
                search_vector = slugify(search_vector)
                # add prefix matching
                search_vector = u'{}:*'.format(search_vector)
                if search_query is None:
                    search_query = SearchQueryToTsquery(search_vector)
                else:
                    search_query &= SearchQueryToTsquery(search_vector)
        return qs.filter(
            **{
                self.name: search_query
            }
        )


class BaseFilterSet(django_filters.FilterSet):
    search_vector = PrefixSearchFilter()
    search_vector_name = PrefixSearchFilter()

    class Meta:
        fields = [
            'search_vector',
            'search_vector_name'
        ]


class CountryFilterSet(BaseFilterSet):
    class Meta(BaseFilterSet.Meta):
        model = Country


class CountrySubareaFilterSet(BaseFilterSet):
    class Meta(BaseFilterSet.Meta):
        model = CountrySubarea


class PlaceFilterSet(BaseFilterSet):
    country_subarea = CenterWithinLocationSlugFilter(location_model=CountrySubarea)

    class Meta(BaseFilterSet.Meta):
        model = Place
        fields = [
            'country_subarea',
        ] + BaseFilterSet.Meta.fields


class PlaceSubareaFilterSet(BaseFilterSet):
    place = CenterWithinLocationSlugFilter(location_model=Place)

    class Meta(BaseFilterSet.Meta):
        model = PlaceSubarea
        fields = [
            'place',
        ] + BaseFilterSet.Meta.fields


class StreetFilterSet(BaseFilterSet):
    place = CenterWithinLocationSlugFilter(location_model=Place)
    place_subarea = CenterWithinLocationSlugFilter(location_model=PlaceSubarea)

    class Meta(BaseFilterSet.Meta):
        model = Street
        fields = [
            'place',
            'place_subarea',
        ] + BaseFilterSet.Meta.fields
