"""The demo page. Part of the local harness, not of the package."""
import os

from django.http import FileResponse, Http404

DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demo')

CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
}


def demo(request, path='index.html'):
    """
    Serve the demo from the same origin as the API.

    Same origin means no CORS to configure, which is the whole reason this is a
    Django view rather than a separate static server.
    """
    full_path = os.path.normpath(os.path.join(DEMO_DIR, path))
    if not full_path.startswith(DEMO_DIR) or not os.path.isfile(full_path):
        raise Http404(path)
    extension = os.path.splitext(full_path)[1]
    return FileResponse(open(full_path, 'rb'), content_type=CONTENT_TYPES.get(extension))
