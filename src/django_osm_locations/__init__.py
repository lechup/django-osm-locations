__version__ = '0.3.0'

#: The oldest version of this package that can load a pack this one makes.
#: Every pack carries it, and the loader refuses a pack that asks for a newer
#: version than itself. Raise it with any change an older reader would get
#: wrong: an index's mapping, or how a pack is laid out. The mappings last
#: changed in 0.2.0.
PACKS_LOAD_WITH = '0.2.0'
