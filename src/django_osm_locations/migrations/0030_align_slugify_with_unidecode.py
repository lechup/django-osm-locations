from django.db import migrations


class Migration(migrations.Migration):
    """
    Align the database-side slugify() with unidecode.

    Search vectors are built in SQL by slugify(), while stored slugs and the
    API's query normalisation are built in Python by
    django.utils.text.slugify(unidecode(name)). The two disagreed: the old
    translate() map covered only part of Latin-1, deleted every unmapped
    character, transposed 'o'/'u' for the circumflex pair, and mapped
    'oe'-style digraphs inconsistently -- so e.g. "Munchen" indexed as
    'munchen' could never be matched by a query normalised to 'muenchen'.

    The table below is GENERATED FROM unidecode over Latin-1 Supplement and
    Latin Extended-A, so the two sides agree by construction.
    """

    dependencies = [
        ('locations', '0029_auto_20171229_1504'),
    ]

    operations = [
        migrations.RunSQL(
            # forwards: unidecode-aligned
            r"""
            CREATE OR REPLACE FUNCTION slugify(str text) RETURNS text AS $$
                BEGIN
                    -- Multi-character transliterations first: translate() is
                    -- strictly 1:1 and cannot expand one character into two.
                    str := replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(str, 'Æ', 'AE'), 'Þ', 'Th'), 'ß', 'ss'), 'æ', 'ae'), 'þ', 'th'), 'Ĳ', 'IJ'), 'ĳ', 'ij'), 'Ŋ', 'NG'), 'ŋ', 'ng'), 'Œ', 'OE'), 'œ', 'oe');
                    -- Single-character transliterations.
                    str := lower(translate(
                        str,
                        'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝàáâãäåçèéêëìíîïðñòóôõöøùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ²³¹',
                        'AAAAAACEEEEIIIIDNOOOOOxOUUUUYaaaaaaceeeeiiiidnoooooouuuuyyAaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjKkkLlLlLlLlLlNnNnNnOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzs231'
                    ));
                    -- Drop anything that is still not slug-safe.
                    str := regexp_replace(str, '[^a-z0-9 -]', '', 'g');
                    -- Collapse runs of spaces/hyphens into a single hyphen and
                    -- trim them from both ends, matching django.utils.text.slugify.
                    str := regexp_replace(str, '[ -]+', '-', 'g');
                    RETURN btrim(str, '-');
                END
            $$ LANGUAGE plpgsql;
            """,
            # backwards: restore the previous definition
            r"""
            CREATE OR REPLACE FUNCTION slugify(str text) RETURNS text AS $$
                BEGIN
                    RETURN regexp_replace(
                       lower(translate(str,
                         'äëïöüáéíóúâêîûôåãõàèìòùřšěčůńýśćłęążźĄŃÝŚĆŁĘÄËÏÖÜÁÉÍÓÚÂÊÎÛÔÅÃÕÀÈÌÒÙŘŠĚČŮŻŹß ²ø®',
                         'aeiouaeiouaeiouaaoaeioursecunyscleazzANYSCLEAEIOUAEIOUAEIOUAAOAEIOURSECUZzs-2dR'
                         -- missing chars will be removed
                       )),
                       -- strip all others chars than [^a-z0-9 \-]
                       '[^a-z0-9 \-]',
                       '',
                       'g'
                    );
                END
            $$ LANGUAGE plpgsql;
            """,
        ),
    ]
