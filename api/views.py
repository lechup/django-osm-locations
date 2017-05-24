# -*- coding: utf-8 -*-
from django.core.exceptions import ObjectDoesNotExist
from django.http.response import Http404
from rest_framework import viewsets
from rest_framework.decorators import detail_route
from rest_framework.response import Response

from .filters import (
    CountryFilterSet, CountrySubareaFilterSet, PlaceFilterSet, PlaceSubareaFilterSet, StreetFilterSet
)
from .serializers import (CountryDefaultSerializer, CountryRetrieveSerializer, CountrySubareaDefaultSerializer,
                          CountrySubareaRetrieveSerializer, PlaceDefaultSerializer, PlaceRetrieveSerializer,
                          PlaceSubareaDefaultSerializer, PlaceSubareaRetrieveSerializer, StreetDefaultSerializer,
                          StreetRetrieveSerializer)
from ..models import Country, CountrySubarea, Place, PlaceSubarea, Street


class BaseLocationViewSet(viewsets.ReadOnlyModelViewSet):
    retrieve_serialize_class = None
    lookup_field = 'slug'
    ordering_fields = ('name',)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return self.retrieve_serialize_class
        return self.serializer_class


class BaseBorderLocationViewSet(BaseLocationViewSet):
    ordering_fields = BaseLocationViewSet.ordering_fields + ('area',)


class CountryViewSet(BaseBorderLocationViewSet):
    filter_class = CountryFilterSet
    queryset = Country.objects.all()
    serializer_class = CountryDefaultSerializer
    retrieve_serialize_class = CountryRetrieveSerializer


class CountrySubareaViewSet(BaseBorderLocationViewSet):
    filter_class = CountrySubareaFilterSet
    queryset = CountrySubarea.objects.all()
    serializer_class = CountrySubareaDefaultSerializer
    retrieve_serialize_class = CountrySubareaRetrieveSerializer


class PlaceViewSet(BaseBorderLocationViewSet):
    filter_class = PlaceFilterSet
    queryset = Place.objects.all()
    serializer_class = PlaceDefaultSerializer
    retrieve_serialize_class = PlaceRetrieveSerializer


class PlaceSubareaViewSet(BaseBorderLocationViewSet):
    filter_class = PlaceSubareaFilterSet
    queryset = PlaceSubarea.objects.all()
    serializer_class = PlaceSubareaDefaultSerializer
    retrieve_serialize_class = PlaceSubareaRetrieveSerializer


class StreetViewSet(BaseBorderLocationViewSet):
    filter_class = StreetFilterSet
    queryset = Street.objects.all()
    serializer_class = StreetDefaultSerializer
    retrieve_serialize_class = StreetRetrieveSerializer
