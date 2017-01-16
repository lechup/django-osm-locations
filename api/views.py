# -*- coding: utf-8 -*-
from django.core.exceptions import ObjectDoesNotExist
from django.http.response import Http404
from rest_framework import viewsets, filters
from rest_framework.decorators import detail_route
from rest_framework.response import Response

from .serializers import (CountryDefaultSerializer, CountryRetrieveSerializer, CountrySubareaDefaultSerializer,
                          CountrySubareaRetrieveSerializer, PlaceDefaultSerializer, PlaceRetrieveSerializer,
                          PlaceSubareaDefaultSerializer, PlaceSubareaRetrieveSerializer, StreetDefaultSerializer,
                          StreetRetrieveSerializer)
from ..models import Country, CountrySubarea, Place, PlaceSubarea, Street


class BaseLocationViewSet(viewsets.ReadOnlyModelViewSet):
    retrieve_serialize_class = None
    filter_backends = (filters.SearchFilter,)
    search_fields = ('name',)
    lookup_field = 'slug'
    ordering_fields = ('name',)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return self.retrieve_serialize_class
        return self.serializer_class

    def get_object_by_slug(self, slug):
        try:
            return self.get_queryset().get(slug=slug)
        except ObjectDoesNotExist:
            raise Http404

    def get_datail_paginated_queryset_response(self, serializer_class, request, detail, slug):
        obj = self.get_object_by_slug(slug)
        queryset = getattr(obj, detail)
        page = self.paginate_queryset(self.filter_queryset(queryset))

        if page is not None:
            serializer = serializer_class(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        serializer = serializer_class(queryset, many=True, context={'request': request})
        return Response(serializer.data)


class BaseBorderLocationViewSet(BaseLocationViewSet):
    ordering_fields = BaseLocationViewSet.ordering_fields + ('area',)


class CountryViewSet(BaseBorderLocationViewSet):
    queryset = Country.objects.all()
    serializer_class = CountryDefaultSerializer
    retrieve_serialize_class = CountryRetrieveSerializer

    @detail_route()
    def subareas(self, request, slug):
        return self.get_datail_paginated_queryset_response(CountrySubareaDefaultSerializer, request, 'subareas', slug)

    @detail_route()
    def places(self, request, slug):
        return self.get_datail_paginated_queryset_response(PlaceDefaultSerializer, request, 'places', slug)

    @detail_route()
    def placesubareas(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            PlaceSubareaDefaultSerializer, request, 'placesubareas', slug)

    @detail_route()
    def streets(self, request, slug):
        return self.get_datail_paginated_queryset_response(StreetDefaultSerializer, request, 'streets', slug)


class CountrySubareaViewSet(BaseBorderLocationViewSet):
    queryset = CountrySubarea.objects.all()
    serializer_class = CountrySubareaDefaultSerializer
    retrieve_serialize_class = CountrySubareaRetrieveSerializer

    @detail_route()
    def near_countrysubareas(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            CountrySubareaDefaultSerializer, request, 'near_countrysubareas', slug)

    @detail_route()
    def near_places(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            PlaceDefaultSerializer, request, 'near_places', slug)

    @detail_route()
    def near_placesubareas(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            PlaceSubareaDefaultSerializer, request, 'near_placesubareas', slug)

    @detail_route()
    def near_streets(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            StreetDefaultSerializer, request, 'near_streets', slug)

    @detail_route()
    def places(self, request, slug):
        return self.get_datail_paginated_queryset_response(PlaceDefaultSerializer, request, 'places', slug)

    @detail_route()
    def streets(self, request, slug):
        return self.get_datail_paginated_queryset_response(StreetDefaultSerializer, request, 'streets', slug)


class PlaceViewSet(BaseBorderLocationViewSet):
    queryset = Place.objects.all()
    serializer_class = PlaceDefaultSerializer
    retrieve_serialize_class = PlaceRetrieveSerializer

    @detail_route()
    def near_places(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            PlaceDefaultSerializer, request, 'near_places', slug)

    @detail_route()
    def near_placesubareas(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            PlaceSubareaDefaultSerializer, request, 'near_placesubareas', slug)

    @detail_route()
    def near_streets(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            StreetDefaultSerializer, request, 'near_streets', slug)

    @detail_route()
    def subareas(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            PlaceSubareaDefaultSerializer, request, 'subareas', slug)

    @detail_route()
    def streets(self, request, slug):
        return self.get_datail_paginated_queryset_response(
            StreetDefaultSerializer, request, 'streets', slug)


class PlaceSubareaViewSet(BaseBorderLocationViewSet):
    queryset = PlaceSubarea.objects.all()
    serializer_class = PlaceSubareaDefaultSerializer
    retrieve_serialize_class = PlaceSubareaRetrieveSerializer

    @detail_route()
    def streets(self, request, slug):
        return self.get_datail_paginated_queryset_response(StreetDefaultSerializer, request, 'streets', slug)


class StreetViewSet(BaseBorderLocationViewSet):
    queryset = Street.objects.all()
    serializer_class = StreetDefaultSerializer
    retrieve_serialize_class = StreetRetrieveSerializer
