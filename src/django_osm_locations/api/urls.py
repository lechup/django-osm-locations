from . import views


def update_router_with_locations(router):
    router.register(r'countries', views.CountryViewSet)
    router.register(r'country-subareas', views.CountrySubareaViewSet)
    router.register(r'places', views.PlaceViewSet)
    router.register(r'place-subareas', views.PlaceSubareaViewSet)
    router.register(r'streets', views.StreetViewSet)
    return router
