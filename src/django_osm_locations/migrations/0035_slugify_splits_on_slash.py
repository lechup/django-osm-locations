"""
A slash separates words; it does not join them.

The function dropped everything outside ``[a-z0-9 -]`` before collapsing
whitespace, so a slash vanished and the words on either side fused.
"Derry/Londonderry" became "derrylondonderry", and Poland's "k/" for koło --
"Nad Kanalem k/Krynki" -- became "nad-kanalem-kkrynki".

This has to keep matching utils.slugify and LocationBase.slugify exactly.
When they disagree the failure is silent: the stored slug and the search
vector are built here, the query is normalised in Python, and a mismatch
means a place simply cannot be found by name.
"""
from django.db import migrations

SPLIT_ON_SLASH = "                    str := replace(str, '/', ' ');\n"

FORWARDS = r"""
CREATE OR REPLACE FUNCTION slugify(str text) RETURNS text AS $$
    BEGIN
        -- A slash separates words rather than joining them, and is not
        -- slug-safe, so it has to become a space before the strip below
        -- removes it and fuses what it separated.
        str := replace(str, '/', ' ');
        -- Multi-character transliterations first: translate() is
        -- strictly 1:1 and cannot expand one character into two.
        str := replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(str, 'Æ', 'Ae'), 'Þ', 'Th'), 'ß', 'ss'), 'æ', 'ae'), 'þ', 'th'), 'Ĳ', 'IJ'), 'ĳ', 'ij'), 'Ŋ', 'Ng'), 'ŋ', 'ng'), 'Œ', 'Oe'), 'œ', 'oe');
        -- Single-character transliterations.
        str := lower(translate(
            str,
            'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝàáâãäåçèéêëìíîïðñòóôõöøùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ²³¹',
            'AAAAAACEEEEIIIIDNOOOOOOUUUUYaaaaaaceeeeiiiidnoooooouuuuyyAaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjKkqLlLlLlLlLlNnNnNnOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzs231'
        ));
        -- Drop anything that is still not slug-safe.
        str := regexp_replace(str, '[^a-z0-9 -]', '', 'g');
        -- Collapse runs of spaces/hyphens into a single hyphen and
        -- trim them from both ends, matching django.utils.text.slugify.
        str := regexp_replace(str, '[ -]+', '-', 'g');
        RETURN btrim(str, '-');
    END
$$ LANGUAGE plpgsql;
"""

BACKWARDS = FORWARDS.replace(
    """        -- A slash separates words rather than joining them, and is not
        -- slug-safe, so it has to become a space before the strip below
        -- removes it and fuses what it separated.
        str := replace(str, '/', ' ');
""",
    "",
)


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0034_add_seat'),
    ]

    operations = [
        migrations.RunSQL(FORWARDS, BACKWARDS),
    ]
