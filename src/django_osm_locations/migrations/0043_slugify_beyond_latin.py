"""
Transliterate Greek, Cyrillic and the rest of Latin, not only Latin-1.

The database slugify() covered Latin-1 and Latin Extended-A and deleted
everything else, while the slugs Python stores go through anyascii, which
covers every script. Greece came out with an empty country slug, so its index
could not be built, and every Greek place a slug of digits; Romania's comma-
below ș and ț, in Latin Extended-B, vanished from its slugs and search vectors.
Cyrillic -- Bulgaria, Serbia, Ukraine, Belarus, North Macedonia -- would have
gone the same way.

GENERATED from anyascii over

* Latin-1 punctuation and signs (U+00A0-U+00BF)
* Latin-1 letters, Latin Extended-A and -B (U+00C0-U+024F)
* IPA Extensions, for Azerbaijani (U+0250-U+02AF)
* Greek (U+0370-U+03FF)
* Cyrillic and Cyrillic Supplement (U+0400-U+052F)
* Armenian (U+0530-U+058F)
* Georgian (U+10A0-U+10FF)
* Latin Extended Additional (U+1E00-U+1EFF)
* Greek Extended (U+1F00-U+1FFF)

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
        IF str ~ '[^\x01-\x7f]' THEN
            -- Multi-character transliterations first: translate() is
            -- strictly 1:1 and cannot expand one character into two.
            str := replace(str, '£', 'GBP');
            str := replace(str, '°', 'deg');
            str := replace(str, '¼', '14');
            str := replace(str, '½', '12');
            str := replace(str, '¾', '34');
            str := replace(str, 'Æ', 'Ae');
            str := replace(str, 'Þ', 'Th');
            str := replace(str, 'ß', 'ss');
            str := replace(str, 'æ', 'ae');
            str := replace(str, 'þ', 'th');
            str := replace(str, 'Ĳ', 'IJ');
            str := replace(str, 'ĳ', 'ij');
            str := replace(str, 'Ŋ', 'Ng');
            str := replace(str, 'ŋ', 'ng');
            str := replace(str, 'Œ', 'Oe');
            str := replace(str, 'œ', 'oe');
            str := replace(str, 'ƕ', 'hw');
            str := replace(str, 'Ƣ', 'Gh');
            str := replace(str, 'ƣ', 'gh');
            str := replace(str, 'Ʃ', 'Sh');
            str := replace(str, 'ƪ', 'sh');
            str := replace(str, 'Ʒ', 'Zh');
            str := replace(str, 'ƺ', 'zh');
            str := replace(str, 'ƻ', 'dz');
            str := replace(str, 'ƾ', 'ts');
            str := replace(str, 'ǂ', 'qc');
            str := replace(str, 'Ǆ', 'DZ');
            str := replace(str, 'ǅ', 'Dz');
            str := replace(str, 'ǆ', 'dz');
            str := replace(str, 'Ǉ', 'LJ');
            str := replace(str, 'ǈ', 'Lj');
            str := replace(str, 'ǉ', 'lj');
            str := replace(str, 'Ǌ', 'NJ');
            str := replace(str, 'ǋ', 'Nj');
            str := replace(str, 'ǌ', 'nj');
            str := replace(str, 'Ǣ', 'Ae');
            str := replace(str, 'ǣ', 'ae');
            str := replace(str, 'Ǯ', 'Zh');
            str := replace(str, 'ǯ', 'zh');
            str := replace(str, 'Ǳ', 'DZ');
            str := replace(str, 'ǲ', 'Dz');
            str := replace(str, 'ǳ', 'dz');
            str := replace(str, 'Ƕ', 'Hw');
            str := replace(str, 'Ǽ', 'Ae');
            str := replace(str, 'ǽ', 'ae');
            str := replace(str, 'Ȣ', 'Ou');
            str := replace(str, 'ȣ', 'ou');
            str := replace(str, 'ȸ', 'db');
            str := replace(str, 'ȹ', 'qp');
            str := replace(str, 'ɚ', 'er');
            str := replace(str, 'ɝ', 'er');
            str := replace(str, 'ɮ', 'lzh');
            str := replace(str, 'ɶ', 'oe');
            str := replace(str, 'ʃ', 'sh');
            str := replace(str, 'ʆ', 'sh');
            str := replace(str, 'ʒ', 'zh');
            str := replace(str, 'ʓ', 'zh');
            str := replace(str, 'ʘ', 'pc');
            str := replace(str, 'ʚ', 'oe');
            str := replace(str, 'ʣ', 'dz');
            str := replace(str, 'ʤ', 'dzh');
            str := replace(str, 'ʥ', 'dz');
            str := replace(str, 'ʦ', 'ts');
            str := replace(str, 'ʧ', 'tsh');
            str := replace(str, 'ʨ', 'tc');
            str := replace(str, 'ʩ', 'fng');
            str := replace(str, 'ʪ', 'ls');
            str := replace(str, 'ʫ', 'lz');
            str := replace(str, 'ʬ', 'ww');
            str := replace(str, 'Θ', 'Th');
            str := replace(str, 'Χ', 'Ch');
            str := replace(str, 'Ψ', 'Ps');
            str := replace(str, 'θ', 'th');
            str := replace(str, 'χ', 'ch');
            str := replace(str, 'ψ', 'ps');
            str := replace(str, 'ϑ', 'th');
            str := replace(str, 'ϕ', 'ph');
            str := replace(str, 'Ϛ', 'St');
            str := replace(str, 'ϛ', 'st');
            str := replace(str, 'Ϣ', 'Sh');
            str := replace(str, 'ϣ', 'sh');
            str := replace(str, 'Ϯ', 'Ti');
            str := replace(str, 'ϯ', 'ti');
            str := replace(str, 'ϴ', 'Th');
            str := replace(str, 'Ϸ', 'Sh');
            str := replace(str, 'ϸ', 'sh');
            str := replace(str, 'Ђ', 'Dj');
            str := replace(str, 'Є', 'Ie');
            str := replace(str, 'Ѕ', 'Dz');
            str := replace(str, 'Љ', 'Lj');
            str := replace(str, 'Њ', 'Nj');
            str := replace(str, 'Џ', 'Dzh');
            str := replace(str, 'Ж', 'Zh');
            str := replace(str, 'Х', 'Kh');
            str := replace(str, 'Ц', 'Ts');
            str := replace(str, 'Ч', 'Ch');
            str := replace(str, 'Ш', 'Sh');
            str := replace(str, 'Щ', 'Shch');
            str := replace(str, 'Ю', 'Yu');
            str := replace(str, 'Я', 'Ya');
            str := replace(str, 'ж', 'zh');
            str := replace(str, 'х', 'kh');
            str := replace(str, 'ц', 'ts');
            str := replace(str, 'ч', 'ch');
            str := replace(str, 'ш', 'sh');
            str := replace(str, 'щ', 'shch');
            str := replace(str, 'ю', 'yu');
            str := replace(str, 'я', 'ya');
            str := replace(str, 'ђ', 'dj');
            str := replace(str, 'є', 'ie');
            str := replace(str, 'ѕ', 'dz');
            str := replace(str, 'љ', 'lj');
            str := replace(str, 'њ', 'nj');
            str := replace(str, 'џ', 'dzh');
            str := replace(str, 'Ѥ', 'Ie');
            str := replace(str, 'ѥ', 'ie');
            str := replace(str, 'Ѩ', 'Ie');
            str := replace(str, 'ѩ', 'ie');
            str := replace(str, 'Ѭ', 'Io');
            str := replace(str, 'ѭ', 'io');
            str := replace(str, 'Ѯ', 'Ks');
            str := replace(str, 'ѯ', 'ks');
            str := replace(str, 'Ѱ', 'Ps');
            str := replace(str, 'ѱ', 'ps');
            str := replace(str, 'Ѿ', 'Ot');
            str := replace(str, 'ѿ', 'ot');
            str := replace(str, '҂', '1000');
            str := replace(str, '҈', '100000');
            str := replace(str, '҉', '1000000');
            str := replace(str, 'Ҋ', 'Jh');
            str := replace(str, 'ҋ', 'jh');
            str := replace(str, 'Ҏ', 'Rh');
            str := replace(str, 'ҏ', 'rh');
            str := replace(str, 'Ғ', 'Gh');
            str := replace(str, 'ғ', 'gh');
            str := replace(str, 'Ҕ', 'Gh');
            str := replace(str, 'ҕ', 'gh');
            str := replace(str, 'Ң', 'Ng');
            str := replace(str, 'ң', 'ng');
            str := replace(str, 'Ҥ', 'Ng');
            str := replace(str, 'ҥ', 'ng');
            str := replace(str, 'Ҧ', 'Ph');
            str := replace(str, 'ҧ', 'ph');
            str := replace(str, 'Ҭ', 'Th');
            str := replace(str, 'ҭ', 'th');
            str := replace(str, 'Ҵ', 'Ts');
            str := replace(str, 'ҵ', 'ts');
            str := replace(str, 'Ҽ', 'Ch');
            str := replace(str, 'ҽ', 'ch');
            str := replace(str, 'Ҿ', 'Ch');
            str := replace(str, 'ҿ', 'ch');
            str := replace(str, 'Ӂ', 'Gi');
            str := replace(str, 'ӂ', 'gi');
            str := replace(str, 'Ӆ', 'Lh');
            str := replace(str, 'ӆ', 'lh');
            str := replace(str, 'Ӈ', 'Ng');
            str := replace(str, 'ӈ', 'ng');
            str := replace(str, 'Ӊ', 'Nh');
            str := replace(str, 'ӊ', 'nh');
            str := replace(str, 'Ӌ', 'Dzh');
            str := replace(str, 'ӌ', 'dzh');
            str := replace(str, 'Ӎ', 'Mh');
            str := replace(str, 'ӎ', 'mh');
            str := replace(str, 'Ӕ', 'Ae');
            str := replace(str, 'ӕ', 'ae');
            str := replace(str, 'Ӝ', 'Dzh');
            str := replace(str, 'ӝ', 'dzh');
            str := replace(str, 'Ӡ', 'Dz');
            str := replace(str, 'ӡ', 'dz');
            str := replace(str, 'Ӵ', 'Tsh');
            str := replace(str, 'ӵ', 'tsh');
            str := replace(str, 'Ӷ', 'Gh');
            str := replace(str, 'ӷ', 'gh');
            str := replace(str, 'Ӻ', 'Xh');
            str := replace(str, 'ӻ', 'xh');
            str := replace(str, 'Ԃ', 'Dj');
            str := replace(str, 'ԃ', 'dj');
            str := replace(str, 'Ԅ', 'Zj');
            str := replace(str, 'ԅ', 'zj');
            str := replace(str, 'Ԇ', 'Dz');
            str := replace(str, 'ԇ', 'dz');
            str := replace(str, 'Ԉ', 'Lj');
            str := replace(str, 'ԉ', 'lj');
            str := replace(str, 'Ԋ', 'Nj');
            str := replace(str, 'ԋ', 'nj');
            str := replace(str, 'Ԍ', 'Sj');
            str := replace(str, 'ԍ', 'sj');
            str := replace(str, 'Ԏ', 'Tj');
            str := replace(str, 'ԏ', 'tj');
            str := replace(str, 'Ԕ', 'Lkh');
            str := replace(str, 'ԕ', 'lkh');
            str := replace(str, 'Ԗ', 'Rkh');
            str := replace(str, 'ԗ', 'rkh');
            str := replace(str, 'Ԥ', 'Ph');
            str := replace(str, 'ԥ', 'ph');
            str := replace(str, 'Ԩ', 'Ny');
            str := replace(str, 'ԩ', 'ny');
            str := replace(str, 'Ԫ', 'Dzh');
            str := replace(str, 'ԫ', 'dzh');
            str := replace(str, 'Ԭ', 'Dch');
            str := replace(str, 'ԭ', 'dch');
            str := replace(str, 'Ժ', 'Zh');
            str := replace(str, 'Խ', 'Kh');
            str := replace(str, 'Ծ', 'Ts');
            str := replace(str, 'Ձ', 'Dz');
            str := replace(str, 'Ղ', 'Gh');
            str := replace(str, 'Ճ', 'Ch');
            str := replace(str, 'Շ', 'Sh');
            str := replace(str, 'Չ', 'Ch');
            str := replace(str, 'Ռ', 'Rr');
            str := replace(str, 'Ց', 'Ts');
            str := replace(str, 'ժ', 'zh');
            str := replace(str, 'խ', 'kh');
            str := replace(str, 'ծ', 'ts');
            str := replace(str, 'ձ', 'dz');
            str := replace(str, 'ղ', 'gh');
            str := replace(str, 'ճ', 'ch');
            str := replace(str, 'շ', 'sh');
            str := replace(str, 'չ', 'ch');
            str := replace(str, 'ռ', 'rr');
            str := replace(str, 'ց', 'ts');
            str := replace(str, 'և', 'ev');
            str := replace(str, '֏', 'Dram');
            str := replace(str, 'Ⴏ', 'Zh');
            str := replace(str, 'Ⴖ', 'Gh');
            str := replace(str, 'Ⴘ', 'Sh');
            str := replace(str, 'Ⴙ', 'Ch');
            str := replace(str, 'Ⴚ', 'Ts');
            str := replace(str, 'Ⴛ', 'Dz');
            str := replace(str, 'Ⴜ', 'Ts');
            str := replace(str, 'Ⴝ', 'Ch');
            str := replace(str, 'Ⴞ', 'Kh');
            str := replace(str, 'Ⴭ', 'Ae');
            str := replace(str, 'ჟ', 'zh');
            str := replace(str, 'ღ', 'gh');
            str := replace(str, 'შ', 'sh');
            str := replace(str, 'ჩ', 'ch');
            str := replace(str, 'ც', 'ts');
            str := replace(str, 'ძ', 'dz');
            str := replace(str, 'წ', 'ts');
            str := replace(str, 'ჭ', 'ch');
            str := replace(str, 'ხ', 'kh');
            str := replace(str, 'ჽ', 'ae');
            str := replace(str, 'ẞ', 'Ss');
            str := replace(str, 'Ỻ', 'Ll');
            str := replace(str, 'ỻ', 'll');
            str := replace(str, 'ἁ', 'ha');
            str := replace(str, 'ἃ', 'ha');
            str := replace(str, 'ἅ', 'ha');
            str := replace(str, 'ἇ', 'ha');
            str := replace(str, 'Ἁ', 'Ha');
            str := replace(str, 'Ἃ', 'Ha');
            str := replace(str, 'Ἅ', 'Ha');
            str := replace(str, 'Ἇ', 'Ha');
            str := replace(str, 'ἑ', 'he');
            str := replace(str, 'ἓ', 'he');
            str := replace(str, 'ἕ', 'he');
            str := replace(str, 'Ἑ', 'He');
            str := replace(str, 'Ἓ', 'He');
            str := replace(str, 'Ἕ', 'He');
            str := replace(str, 'ἡ', 'hi');
            str := replace(str, 'ἣ', 'hi');
            str := replace(str, 'ἥ', 'hi');
            str := replace(str, 'ἧ', 'hi');
            str := replace(str, 'Ἡ', 'Hi');
            str := replace(str, 'Ἣ', 'Hi');
            str := replace(str, 'Ἥ', 'Hi');
            str := replace(str, 'Ἧ', 'Hi');
            str := replace(str, 'ἱ', 'hi');
            str := replace(str, 'ἳ', 'hi');
            str := replace(str, 'ἵ', 'hi');
            str := replace(str, 'ἷ', 'hi');
            str := replace(str, 'Ἱ', 'Hi');
            str := replace(str, 'Ἳ', 'Hi');
            str := replace(str, 'Ἵ', 'Hi');
            str := replace(str, 'Ἷ', 'Hi');
            str := replace(str, 'ὁ', 'ho');
            str := replace(str, 'ὃ', 'ho');
            str := replace(str, 'ὅ', 'ho');
            str := replace(str, 'Ὁ', 'Ho');
            str := replace(str, 'Ὃ', 'Ho');
            str := replace(str, 'Ὅ', 'Ho');
            str := replace(str, 'ὑ', 'hy');
            str := replace(str, 'ὓ', 'hy');
            str := replace(str, 'ὕ', 'hy');
            str := replace(str, 'ὗ', 'hy');
            str := replace(str, 'Ὑ', 'Hy');
            str := replace(str, 'Ὓ', 'Hy');
            str := replace(str, 'Ὕ', 'Hy');
            str := replace(str, 'Ὗ', 'Hy');
            str := replace(str, 'ὡ', 'ho');
            str := replace(str, 'ὣ', 'ho');
            str := replace(str, 'ὥ', 'ho');
            str := replace(str, 'ὧ', 'ho');
            str := replace(str, 'Ὡ', 'Ho');
            str := replace(str, 'Ὣ', 'Ho');
            str := replace(str, 'Ὥ', 'Ho');
            str := replace(str, 'Ὧ', 'Ho');
            str := replace(str, 'ᾁ', 'ha');
            str := replace(str, 'ᾃ', 'ha');
            str := replace(str, 'ᾅ', 'ha');
            str := replace(str, 'ᾇ', 'ha');
            str := replace(str, 'ᾉ', 'Ha');
            str := replace(str, 'ᾋ', 'Ha');
            str := replace(str, 'ᾍ', 'Ha');
            str := replace(str, 'ᾏ', 'Ha');
            str := replace(str, 'ᾑ', 'hi');
            str := replace(str, 'ᾓ', 'hi');
            str := replace(str, 'ᾕ', 'hi');
            str := replace(str, 'ᾗ', 'hi');
            str := replace(str, 'ᾙ', 'Hi');
            str := replace(str, 'ᾛ', 'Hi');
            str := replace(str, 'ᾝ', 'Hi');
            str := replace(str, 'ᾟ', 'Hi');
            str := replace(str, 'ᾡ', 'ho');
            str := replace(str, 'ᾣ', 'ho');
            str := replace(str, 'ᾥ', 'ho');
            str := replace(str, 'ᾧ', 'ho');
            str := replace(str, 'ᾩ', 'Ho');
            str := replace(str, 'ᾫ', 'Ho');
            str := replace(str, 'ᾭ', 'Ho');
            str := replace(str, 'ᾯ', 'Ho');
            str := replace(str, 'ῥ', 'rh');
            str := replace(str, 'Ῥ', 'Rh');
            -- Single-character transliterations.
            str := translate(
                str,
                '¢¥§©ª®²³µ¶¹ºÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝàáâãäåçèéêëìíîïðñòóôõöøùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŉŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſƀƁƂƃƄƅƆƇƈƉƊƋƌƍƎƏƐƑƒƓƔƖƗƘƙƚƛƜƝƞƟƠơƤƥƦƧƨƫƬƭƮƯưƱƲƳƴƵƶƼƽƿǀǁǃǍǎǏǐǑǒǓǔǕǖǗǘǙǚǛǜǝǞǟǠǡǤǥǦǧǨǩǪǫǬǭǰǴǵǷǸǹǺǻǾǿȀȁȂȃȄȅȆȇȈȉȊȋȌȍȎȏȐȑȒȓȔȕȖȗȘșȚțȜȝȞȟȠȡȤȥȦȧȨȩȪȫȬȭȮȯȰȱȲȳȴȵȶȷȺȻȼȽȾȿɀɃɄɅɆɇɈɉɊɋɌɍɎɏɐɑɒɓɔɕɖɗɘəɛɜɞɟɠɡɢɣɤɥɦɧɨɩɪɫɬɭɯɰɱɲɳɴɵɷɸɹɺɻɼɽɾɿʀʁʂʄʅʇʈʉʊʋʌʍʎʏʐʑʖʗʙʛʜʝʞʟʠʮʯͰͱͲͳͶͷͺͻͼͽͿΆΈΉΊΌΎΏΐΑΒΓΔΕΖΗΙΚΛΜΝΞΟΠΡΣΤΥΦΩΪΫάέήίΰαβγδεζηικλμνξοπρςστυφωϊϋόύώϐϒϓϔϖϘϙϜϝϞϟϠϡϤϥϦϧϨϩϪϫϬϭϰϱϲϳϵ϶ϹϺϻϼϽϾϿЀЁЃІЇЈЋЌЍЎАБВГДЕЗИЙКЛМНОПРСТУФЫЭабвгдезийклмнопрстуфыэѐёѓіїјћќѝўѠѡѢѣѦѧѪѫѲѳѴѵѶѷѸѹѺѻѼѽҀҁҐґҖҗҘҙҚқҜҝҞҟҠҡҨҩҪҫҮүҰұҲҳҶҷҸҹҺһӀӃӄӏӐӑӒӓӖӗӘәӚӛӞӟӢӣӤӥӦӧӨөӪӫӬӭӮӯӰӱӲӳӸӹӼӽӾӿԀԁԐԑԒԓԘԙԚԛԜԝԞԟԠԡԢԣԮԯԱԲԳԴԵԶԷԸԹԻԼԿՀՄՅՆՈՊՋՍՎՏՐՒՓՔՕՖՠաբգդեզէըթիլկհմյնոպջսվտրւփքօֆֈႠႡႢႣႤႥႦႧႨႩႪႫႬႭႮႰႱႲႳႴႵႷႿჀჁჂჃჄჅჇაბგდევზთიკლმნოპრსტუფქყჯჰჱჲჳჴჵჶჷჹჼჿḀḁḂḃḄḅḆḇḈḉḊḋḌḍḎḏḐḑḒḓḔḕḖḗḘḙḚḛḜḝḞḟḠḡḢḣḤḥḦḧḨḩḪḫḬḭḮḯḰḱḲḳḴḵḶḷḸḹḺḻḼḽḾḿṀṁṂṃṄṅṆṇṈṉṊṋṌṍṎṏṐṑṒṓṔṕṖṗṘṙṚṛṜṝṞṟṠṡṢṣṤṥṦṧṨṩṪṫṬṭṮṯṰṱṲṳṴṵṶṷṸṹṺṻṼṽṾṿẀẁẂẃẄẅẆẇẈẉẊẋẌẍẎẏẐẑẒẓẔẕẖẗẘẙẚẛẜẝẟẠạẢảẤấẦầẨẩẪẫẬậẮắẰằẲẳẴẵẶặẸẹẺẻẼẽẾếỀềỂểỄễỆệỈỉỊịỌọỎỏỐốỒồỔổỖỗỘộỚớỜờỞởỠỡỢợỤụỦủỨứỪừỬửỮữỰựỲỳỴỵỶỷỸỹỼỽỾỿἀἂἄἆἈἊἌἎἐἒἔἘἚἜἠἢἤἦἨἪἬἮἰἲἴἶἸἺἼἾὀὂὄὈὊὌὐὒὔὖὠὢὤὦὨὪὬὮὰάὲέὴήὶίὸόὺύὼώᾀᾂᾄᾆᾈᾊᾌᾎᾐᾒᾔᾖᾘᾚᾜᾞᾠᾢᾤᾦᾨᾪᾬᾮᾰᾱᾲᾳᾴᾶᾷᾸᾹᾺΆᾼιῂῃῄῆῇῈΈῊΉῌῐῑῒΐῖῗῘῙῚΊ῝῞῟ῠῡῢΰῤῦῧῨῩῪΎῲῳῴῶῷῸΌῺΏῼ῾',
                'cYSCaR23uP1oAAAAAACEEEEIIIIDNOOOOOOUUUUYaaaaaaceeeeiiiidnoooooouuuuyyAaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjKkqLlLlLlLlLlNnNnNnnOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzsbBBb66OCcDDDdzEEEFfGGIIKkllWNnOOoPpR22tTtTUuUVYyZz55wcxqAaIiOoUuUuUuUuUueAaAaGgGgKkOoOojGgWNnAaOoAaAaEeEeIiIiOoOoRrRrUuUuSsTtYyHhNdZzAaEeOoOoOoOoYylntjACcLTszBUAEeJjQqRrYyaaabocddeeeeojgggguyhhiiilllwwmnnnoufrrrrrrirrsjittuuvawyyzzxqbghjklqyyHhSsWwisssJAEIIOYOiAVGDEZIIKLMNXOPRSTYFOIYaeiiyavgdeziiklmnxoprsstyfoiyoyobYYYpQqWwQqSsFfXxHhJjCckrsjeeSSsrSSSEEGIIJCKIUABVGDEZIYKLMNOPRSTUFYEabvgdeziyklmnoprstufyeeegiijckiuOoEeEeOoFfYyYyUuOoOoQqGgJjZzQqGgQqQqWwSsUuUuHhJjCcHhHQqhAaAaEeAaEeJjIiIiOoOoYyAaUuUuUuYyXxHhDdAaLlAaQqWwQqLlNnLlABGDEZEYTILKHMYNOPJSVTRWPKOFaabgdezeytilkhmynopjsvtrwpkofhABGDEVZTIKLMNOPRSTUPKQJHEYWXOEabgdevztiklmnoprstupkqjheywxofegnwAaBbBbBbCcDdDdDdDdDdEeEeEeEeEeFfGgHhHhHhHhHhIiIiKkKkKkLlLlLlLlMmMmMmNnNnNnNnOoOoOoOoPpPpRrRrRrRrSsSsSsSsSsTtTtTtTtUuUuUuUuUuVvVvWwWwWwWwWwXxXxYyZzZzZzhtwyasssdAaAaAaAaAaAaAaAaAaAaAaAaEeEeEeEeEeEeEeEeIiIiOoOoOoOoOoOoOoOoOoOoOoOoUuUuUuUuUuUuUuYyYyYyYyVvYyaaaaAAAAeeeEEEiiiiIIIIiiiiIIIIoooOOOyyyyooooOOOOaaeeiiiiooyyooaaaaAAAAiiiiIIIIooooOOOOaaaaaaaAAAAAiiiiiiEEIIIiiiiiiIIIIhhhyyyyryyYYYYoooooOOOOOh'
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
