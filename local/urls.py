from django.urls import include, path

from django_osm_locations.api.urls import urlpatterns as locations_urls

from .views import demo

urlpatterns = [
    path('api/', include(locations_urls)),
    path('', demo, name='demo'),
    path('<str:path>', demo),
]
