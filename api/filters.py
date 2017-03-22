import django_filters
from django.contrib.postgres.search import SearchQuery

from ..utils.slugify import slugify
from ..models import Country, CountrySubarea, Place, PlaceSubarea, Street


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


def get_search_queryset(queryset, name, value):
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
    return queryset.filter(
        search_vector=search_query
    )


class SearchFilterBase(django_filters.FilterSet):
    search = django_filters.CharFilter(name='search', method=get_search_queryset)

    class Meta:
        fields = ['search']


class SearchFilterCountry(SearchFilterBase):
    class Meta(SearchFilterBase.Meta):
        model = Country


class SearchFilterCountrySubarea(SearchFilterBase):
    class Meta(SearchFilterBase.Meta):
        model = CountrySubarea


class SearchFilterPlace(SearchFilterBase):
    class Meta(SearchFilterBase.Meta):
        model = Place


class SearchFilterPlaceSubarea(SearchFilterBase):
    class Meta(SearchFilterBase.Meta):
        model = PlaceSubarea


class SearchFilterStreet(SearchFilterBase):
    class Meta(SearchFilterBase.Meta):
        model = Street
