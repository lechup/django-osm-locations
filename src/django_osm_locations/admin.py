from django.contrib.gis import admin

from .models import Country, CountrySubarea, Place, PlaceSubarea, Street


class CountryAdmin(admin.GISModelAdmin):
    search_fields = ['id', 'name', 'iso2', 'iso3', 'un']
    list_display = ['id', 'name', 'iso2', 'iso3', 'un']
admin.site.register(Country, CountryAdmin)


class CountryRegionAdmin(admin.GISModelAdmin):
    search_fields = ['id', 'name']
    list_display = ['id', 'name']
admin.site.register(CountrySubarea, CountryRegionAdmin)


class CityAdmin(admin.GISModelAdmin):
    search_fields = ['id', 'name']
    list_display = ['id', 'name']
admin.site.register(Place, CityAdmin)


class CitySubareaAdmin(admin.GISModelAdmin):
    search_fields = ['id', 'name']
    list_display = ['id', 'name']
admin.site.register(PlaceSubarea, CitySubareaAdmin)


class StreetAdmin(admin.GISModelAdmin):
    search_fields = ['id', 'name']
    list_display = ['id', 'name']
admin.site.register(Street, StreetAdmin)
