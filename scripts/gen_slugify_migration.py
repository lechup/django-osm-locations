"""Write migration 0043 from anyascii. Re-run to regenerate; never edit the output."""
import re
from anyascii import anyascii

RANGES = [
    (0x00A0, 0x00BF, 'Latin-1 punctuation and signs'),
    (0x00C0, 0x024F, 'Latin-1 letters, Latin Extended-A and -B'),
    (0x0250, 0x02AF, 'IPA Extensions, for Azerbaijani'),
    (0x0370, 0x03FF, 'Greek'),
    (0x0400, 0x052F, 'Cyrillic and Cyrillic Supplement'),
    (0x0530, 0x058F, 'Armenian'),
    (0x10A0, 0x10FF, 'Georgian'),
    (0x1E00, 0x1EFF, 'Latin Extended Additional'),
    (0x1F00, 0x1FFF, 'Greek Extended'),
]
single, multi = [], []
for lo, hi, _ in RANGES:
    for cp in range(lo, hi + 1):
        ch = chr(cp)
        if not ch.isprintable() or ch in "'/":
            continue
        # what slugify keeps of it anyway; a quote or a dot would only be
        # stripped again, and in SQL a quote needs escaping
        out = re.sub('[^A-Za-z0-9 -]', '', anyascii(ch))
        if not re.search('[A-Za-z0-9]', out) or out == ch:
            continue
        (single if len(out) == 1 else multi).append((ch, out))

src = ''.join(c for c, _ in single)
tgt = ''.join(o for _, o in single)
replaces = '\n'.join("            str := replace(str, '{}', '{}');".format(c, o) for c, o in multi)
ranges = '\n'.join('* {} (U+{:04X}-U+{:04X})'.format(name, lo, hi) for lo, hi, name in RANGES)

TEMPLATE = '''"""
Transliterate Greek, Cyrillic and the rest of Latin, not only Latin-1.

The database slugify() covered Latin-1 and Latin Extended-A and deleted
everything else, while the slugs Python stores go through anyascii, which
covers every script. Greece came out with an empty country slug, so its index
could not be built, and every Greek place a slug of digits; Romania's comma-
below ș and ț, in Latin Extended-B, vanished from its slugs and search vectors.
Cyrillic -- Bulgaria, Serbia, Ukraine, Belarus, North Macedonia -- would have
gone the same way.

GENERATED from anyascii over

{ranges}

by scripts/gen_slugify_migration.py; tests/test_slugify.py checks every mapping
against anyascii and the whole function against the Python slugify on real
names. Regenerate it rather than editing it.

A name in plain ASCII -- most of every Latin-script country -- skips the
transliteration entirely, so the three hundred replace() calls cost nothing
where they do nothing.
"""
from django.db import migrations

FORWARDS = r"""
CREATE OR REPLACE FUNCTION slugify(str text) RETURNS text AS $$
    BEGIN
        -- A slash separates words rather than joining them, and is not
        -- slug-safe, so it has to become a space before the strip below
        -- removes it and fuses what it separated.
        str := replace(str, '/', ' ');
        IF str ~ '[^\\x01-\\x7f]' THEN
            -- Multi-character transliterations first: translate() is
            -- strictly 1:1 and cannot expand one character into two.
{replaces}
            -- Single-character transliterations.
            str := translate(
                str,
                '{src}',
                '{tgt}'
            );
        END IF;
        str := lower(str);
        -- Drop anything that is still not slug-safe.
        str := regexp_replace(str, '[^a-z0-9 -]', '', 'g');
        -- Collapse runs of spaces/hyphens into a single hyphen and
        -- trim them from both ends, matching django.utils.text.slugify.
        str := regexp_replace(str, '[ -]+', '-', 'g');
        RETURN btrim(str, '-');
    END
$$ LANGUAGE plpgsql;
"""


def backwards_sql():
    from importlib import import_module

    return import_module(
        'django_osm_locations.migrations.0035_slugify_splits_on_slash').FORWARDS


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0042_country_slug_index'),
    ]

    operations = [
        migrations.RunSQL(FORWARDS, backwards_sql()),
    ]
'''
out = TEMPLATE.format(ranges=ranges, replaces=replaces, src=src, tgt=tgt)
path = 'src/django_osm_locations/migrations/0043_slugify_beyond_latin.py'
open(path, 'w', encoding='utf-8').write(out)
print(path, len(single), len(multi))
