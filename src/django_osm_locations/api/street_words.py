"""
The words of a street's kind, as people abbreviate them.

A query does not say which country it is about, so the tables of every
language apply together. That is why none of this is ever required of a
result: ``ln`` is a lane in England and a laan in the Netherlands, and a
name that has neither may still be the one meant. What an abbreviation
stands for only ranks -- see ``queries.read_query``.

Taken from how addresses are written in each language, and checked against
the street names of the Poland and Germany packs. ``st`` is left out on
purpose: it is street, saint, sankt and sint, and as typed it is already
the start of three of them.
"""
import itertools
import re
import unicodedata

#: An abbreviation, without its dots and in lower case, and what it stands for.
ABBREVIATIONS = {
    # Polish
    'al': ('aleja',),
    'os': ('osiedle',),
    'pl': ('plac', 'platz', 'place'),
    # German, Dutch, Romanian
    'str': ('straße', 'straat', 'strada'),
    # English
    'rd': ('road',),
    'ave': ('avenue',),
    'blvd': ('boulevard',),
    'ln': ('lane', 'laan'),
    'ct': ('court',),
    'hwy': ('highway',),
    'pkwy': ('parkway',),
    'sq': ('square',),
    'ter': ('terrace',),
    'cres': ('crescent',),
    # French
    'av': ('avenue', 'avenida'),
    'bd': ('boulevard',),
    'bld': ('boulevard',),
    'rte': ('route',),
    'fg': ('faubourg',),
    'imp': ('impasse',),
    # Spanish
    'avda': ('avenida',),
    'pza': ('plaza',),
    'plza': ('plaza',),
    'ctra': ('carretera',),
    'pso': ('paseo',),
    # Italian, written v.le, p.zza, c.so, l.go, p.le
    'vle': ('viale',),
    'pzza': ('piazza',),
    'cso': ('corso',),
    'lgo': ('largo',),
    'ple': ('piazzale',),
    # Portuguese
    'pca': ('praça',),
    'tv': ('travessa',),
    'lg': ('largo',),
    # Dutch
    'wg': ('weg',),
    'pln': ('plein',),
    # Hungarian
    'krt': ('körút',),
    # Czech, Slovak
    'nam': ('náměstí', 'námestie'),
    'tr': ('třída',),
}

#: Abbreviations also read as such without a dot, as English, French and
#: Spanish write them. The rest are too easily the start of a name still
#: being typed: "al" of Aleksandra, "tr" of Trakt, "os" of Ostrów.
WITHOUT_A_DOT = frozenset({
    'str', 'rd', 'ave', 'blvd', 'ln', 'ct', 'hwy', 'pkwy', 'sq', 'bd', 'avda', 'ctra',
})

#: Words a name may carry or leave out -- Osiedle Tysiąclecia is as often
#: just Tysiąclecia, in the data and as typed -- so they and their
#: abbreviations are required only until nothing is found with them.
LEFT_OUT = frozenset({'aleja', 'osiedle', 'plac'})

# v.le, p.zza: the dot is inside, so they are read before the words are
DOTTED_INSIDE = re.compile(r'(?<!\w)(v\.le|p\.zza|p\.le|c\.so|l\.go)(?!\w)\.?', re.IGNORECASE)
# a word of letters and the dot after it, if there is one
WORD = re.compile(r'(?<!\w)([^\W\d_]+)(?!\w)(\.?)')
# Hauptstr., Hauptstr: Straße written short at the end of the word it is
# glued to. Nothing else in German ends in -str.
GLUED_STR = re.compile(r'(?<!\w)([^\W\d_]{3,})str(?!\w)\.?', re.IGNORECASE)

#: How many ways of writing a query out are tried as whole names.
MOST_VARIANTS = 4


# Letters that are no letter and an accent to Unicode, but are to the index.
UNDECOMPOSED = str.maketrans({
    'ß': 'ss', 'ł': 'l', 'ø': 'o', 'đ': 'd', 'ð': 'd', 'þ': 'th', 'æ': 'ae', 'œ': 'oe',
    'ı': 'i', 'ħ': 'h',
})


def fold(text):
    """Lower case and without accents, as the index compares words (asciifolding)."""
    text = unicodedata.normalize('NFKD', text.lower().translate(UNDECOMPOSED))
    return ''.join(c for c in text if not unicodedata.combining(c))


def _is_a_start(key, expansions):
    """Whether the abbreviation, as typed, is the start of every word it stands for."""
    return all(fold(word).startswith(key) for word in expansions)


def read_street_words(text):
    """
    ``text`` with its street words read: (required, relaxed, variants, typed).

    ``required`` is the text without the words that cannot be required: an
    abbreviation that is not the start of its word -- "Rd" finds no Road. An
    abbreviation that is the start of its word stays: "Str." finds Straße,
    Straat and Strada as a prefix of each, and requiring it loses nothing.

    ``relaxed`` is ``required`` without the words in ``LEFT_OUT``,
    abbreviated or whole: what to require when nothing has those.

    ``variants`` are the text with each abbreviation written out, one for each
    reading up to ``MOST_VARIANTS``, the likeliest first.

    ``typed`` are the abbreviations left out of ``required`` that had no dot:
    each may be a name still being typed -- "Rd" of Rdzawa -- so they score.
    """
    text = DOTTED_INSIDE.sub(lambda m: m.group(1).replace('.', '') + '. ', text)
    words = [w for w in WORD.findall(text)]
    several = len(words) > 1

    required, relaxed, pieces, typed = [], [], [], []
    position = 0
    for match in WORD.finditer(text):
        word, dot = match.group(1), match.group(2)
        key = fold(word)
        before = text[position:match.start()]
        position = match.end()
        required.append(before)
        relaxed.append(before)
        pieces.append((before,))
        expansions = None
        if key in ABBREVIATIONS and (dot or (key in WITHOUT_A_DOT and several)):
            expansions = ABBREVIATIONS[key]
        if expansions:
            if not _is_a_start(key, expansions):
                if not dot:
                    typed.append(word)
            else:
                required.append(word + dot)
                if not LEFT_OUT.intersection(expansions):
                    relaxed.append(word + dot)
            pieces.append(tuple(e + ' ' for e in expansions))
        elif key in LEFT_OUT and several:
            required.append(word + dot)
            pieces.append((word + dot,))
        else:
            glued = GLUED_STR.fullmatch(word + dot)
            required.append(word + dot)
            relaxed.append(word + dot)
            if glued:
                pieces.append((glued.group(1) + 'straße ',))
            else:
                pieces.append((word + dot,))
        required.append(' ')
        relaxed.append(' ')
    rest = text[position:]
    required.append(rest)
    relaxed.append(rest)
    pieces.append((rest,))

    variants = []
    for chosen in itertools.product(*pieces):
        variant = ''.join(chosen)
        if variant not in variants:
            variants.append(variant)
        if len(variants) == MOST_VARIANTS:
            break
    return ''.join(required), ''.join(relaxed), variants, typed
