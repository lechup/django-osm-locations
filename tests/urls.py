from django.urls import include, path

from django_osm_locations.api.urls import urlpatterns as locations_urls

urlpatterns = [path('api/', include(locations_urls))]
