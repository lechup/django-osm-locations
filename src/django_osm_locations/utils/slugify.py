"""
Slug normalisation for search queries.

This must produce exactly what is stored and indexed, or prefix search silently
returns nothing:

* ``LocationBase.slugify`` stores ``django.utils.text.slugify(unidecode(name))``
* the database ``slugify()`` function builds ``search_vector`` /
  ``search_vector_name`` and is generated from the same unidecode table (see
  migration ``0030_align_slugify_with_unidecode``)
* this module normalises the incoming query in ``api.filters.PrefixSearchFilter``

All three therefore go through unidecode. The previous implementation kept a
hand-maintained character map here, which disagreed with the other two -- it
transliterated German umlauts as "ue"/"oe" while the stored slug and the index
used "u"/"o", so no umlaut place could ever be found by name.
"""
from django.template.defaultfilters import slugify as django_slugify
from unidecode import unidecode


def slugify(string, do_slugify=True, overwrite_char_map=None):
    """
    Transliterate ``string`` to ASCII and slugify it.

    :param do_slugify: apply Django's slugify on top of the transliteration.
    :param overwrite_char_map: optional ``{char: replacement}`` applied before
        transliteration. Scoped to this call only -- it used to mutate a
        module-level dict, which silently corrupted every later call in the
        process.
    """
    if overwrite_char_map:
        for replace_from, replace_to in overwrite_char_map.items():
            string = string.replace(replace_from, replace_to)

    string = unidecode(string)

    if do_slugify:
        string = django_slugify(string)

    return string
