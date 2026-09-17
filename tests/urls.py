from django.urls import include, path
from rest_framework import routers

from django_osm_locations.api.urls import update_router_with_locations

router = update_router_with_locations(routers.DefaultRouter())

urlpatterns = [path('api/', include(router.urls))]
