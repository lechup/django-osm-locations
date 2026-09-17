from django.conf import settings
from django.http import HttpResponsePermanentRedirect, Http404
from django.shortcuts import get_object_or_404

from .models import Place


class PlaceSubdomainMiddleware(object):
    u"""
        middleware:
        * checks for place existence in location.Place model
        * sets request.subdomain to current subdomain value
        * sets request.place to current subdomain value
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.subdomain = self.__class__.get_subdomain_from_host(request)
        request.META['HTTP_X_SUBDOMAIN'] = request.subdomain

        if request.subdomain is False:
            return HttpResponsePermanentRedirect('{}://{}'.format(
                self.__class__.get_protocol(request),
                settings.ROOT_URL
            ))
        elif request.subdomain:
            place = None
            try:
                place = get_object_or_404(
                    Place.objects.all(),
                    slug=request.subdomain
                )
            except Http404:
                if '-' in request.subdomain:
                    main_subdomain = request.subdomain.split('-')
                    if len(main_subdomain) == 2:
                        main_subdomain = main_subdomain[0]
                        place = get_object_or_404(
                            Place.objects.all(),
                            slug=main_subdomain
                        )
                        return HttpResponsePermanentRedirect('{}://{}.{}{}'.format(
                            self.__class__.get_protocol(request),
                            main_subdomain,
                            settings.ROOT_URL,
                            request.get_full_path()
                        ))
                raise
            finally:
                request.place = place

        response = self.get_response(request)
        response['X-Subdomain'] = request.subdomain
        return response

    @staticmethod
    def get_subdomain_from_host(request):
        subdomain = request.get_host().split('.')
        if len(subdomain) == 3:
            return subdomain[0]
        elif len(subdomain) == 2:
            return None
        else:
            return False

    @classmethod
    def get_protocol(cls, request):
        return 'https' if request.is_secure() else 'http'
