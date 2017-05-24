# -*- coding: utf-8 -*-
from rest_framework import serializers

from ..models import Country, CountrySubarea, Place, PlaceSubarea, Street


LOCATION_BASE_SERIALIZER_FIELDS = (
    'url', 'id', 'name', 'slug', 'bbox'
)
LOCATION_BORDER_BASE_SERIALIZER_FIELDS = (
    'center', 'area'
)
LOCATION_MULTILINE_BASE_SERIALIZER_FIELDS = (
    'center', 'length'
)
LOCATION_BORDER_BASE_RETRIEVE_SERIALIZER_FIELDS = (
    'border',
)
LOCATION_MULTILINE_BASE_RETRIEVE_SERIALIZER_FIELDS = (
    'lines',
)
LOCATION_OSM_BASE_SERIALIZER_FIELDS = (
    'node_osm_id', 'way_osm_id', 'relation_osm_id'
)
LOCATION_OSM_BORDER_BASE_SERIALIZER_FIELDS = LOCATION_BASE_SERIALIZER_FIELDS + \
    LOCATION_BORDER_BASE_SERIALIZER_FIELDS + LOCATION_OSM_BASE_SERIALIZER_FIELDS
LOCATION_OSM_MULTILINE_BASE_SERIALIZER_FIELDS = LOCATION_BASE_SERIALIZER_FIELDS + \
    LOCATION_MULTILINE_BASE_SERIALIZER_FIELDS + LOCATION_OSM_BASE_SERIALIZER_FIELDS


class CountryBaseSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='country-detail',
        lookup_field='slug'
    )

    class Meta:
        model = Country
        fields = LOCATION_BASE_SERIALIZER_FIELDS + LOCATION_BORDER_BASE_SERIALIZER_FIELDS + \
            ('iso2', 'iso3', 'un', 'population')


class CountryDefaultSerializer(CountryBaseSerializer):
    class Meta(CountryBaseSerializer.Meta):
        pass


class CountryRetrieveSerializer(CountryBaseSerializer):
    class Meta(CountryBaseSerializer.Meta):
        fields = CountryBaseSerializer.Meta.fields + LOCATION_BORDER_BASE_RETRIEVE_SERIALIZER_FIELDS


class CountrySubareaBaseSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='countrysubarea-detail',
        lookup_field='slug'
    )

    class Meta:
        model = CountrySubarea
        fields = LOCATION_OSM_BORDER_BASE_SERIALIZER_FIELDS + CountrySubarea.denorm_fields


class CountrySubareaDefaultSerializer(CountrySubareaBaseSerializer):
    class Meta(CountrySubareaBaseSerializer.Meta):
        pass


class CountrySubareaRetrieveSerializer(CountrySubareaBaseSerializer):
    class Meta(CountrySubareaBaseSerializer.Meta):
        fields = CountrySubareaBaseSerializer.Meta.fields + LOCATION_BORDER_BASE_RETRIEVE_SERIALIZER_FIELDS


class PlaceBaseSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='place-detail',
        lookup_field='slug'
    )

    class Meta:
        model = Place
        fields = LOCATION_OSM_BORDER_BASE_SERIALIZER_FIELDS + Place.denorm_fields


class PlaceDefaultSerializer(PlaceBaseSerializer):
    class Meta(PlaceBaseSerializer.Meta):
        pass


class PlaceRetrieveSerializer(PlaceBaseSerializer):
    class Meta(PlaceBaseSerializer.Meta):
        fields = PlaceBaseSerializer.Meta.fields + LOCATION_BORDER_BASE_RETRIEVE_SERIALIZER_FIELDS


class PlaceSubareaBaseSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='placesubarea-detail',
        lookup_field='slug'
    )

    class Meta:
        model = PlaceSubarea
        fields = LOCATION_OSM_BORDER_BASE_SERIALIZER_FIELDS + PlaceSubarea.denorm_fields


class PlaceSubareaDefaultSerializer(PlaceSubareaBaseSerializer):
    class Meta(PlaceSubareaBaseSerializer.Meta):
        pass


class PlaceSubareaRetrieveSerializer(PlaceSubareaBaseSerializer):
    class Meta(PlaceSubareaBaseSerializer.Meta):
        fields = PlaceSubareaBaseSerializer.Meta.fields + LOCATION_BORDER_BASE_RETRIEVE_SERIALIZER_FIELDS


class StreetBaseSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='street-detail',
        lookup_field='slug'
    )

    class Meta:
        model = Street
        fields = LOCATION_OSM_MULTILINE_BASE_SERIALIZER_FIELDS + Street.denorm_fields


class StreetDefaultSerializer(StreetBaseSerializer):
    class Meta(StreetBaseSerializer.Meta):
        pass


class StreetRetrieveSerializer(StreetBaseSerializer):
    class Meta(StreetBaseSerializer.Meta):
        fields = StreetBaseSerializer.Meta.fields + LOCATION_MULTILINE_BASE_RETRIEVE_SERIALIZER_FIELDS
