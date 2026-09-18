"""
OpenSearch index built from PostGIS.

PostGIS stays the source of truth: the OSM import writes there, and the index is
derived from it and rebuilt whenever the data changes. Nothing in here writes
back to the database.
"""
from .client import get_client, get_index_prefix  # noqa: F401
from .indexes import INDEXES, LocationIndex  # noqa: F401
