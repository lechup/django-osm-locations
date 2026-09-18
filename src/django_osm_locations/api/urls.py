"""
URLs for the OpenSearch-backed API.

Wire into a project with::

    from django_osm_locations.api.urls import urlpatterns as locations_urls

    urlpatterns = [path('api/', include(locations_urls))]
"""
from django.urls import path

from ..search.indexes import INDEXES
from .views import DetailView, SearchView, type_view

urlpatterns = [
    path('search/', SearchView.as_view(), name='locations-search'),
]

# one listing endpoint per location type, e.g. /places/, /streets/
for _name in INDEXES:
    urlpatterns.append(
        path('{}/'.format(_name), type_view(_name).as_view(), name='locations-{}'.format(_name))
    )

urlpatterns.append(
    path('<str:location_type>/<str:slug>/', DetailView.as_view(), name='locations-detail')
)
