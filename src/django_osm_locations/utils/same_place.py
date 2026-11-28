"""
Is the node a border names -- its label, its seat -- the place the border is?

A municipality's relation carries a node as ``label`` or ``admin_centre``,
and for a town that node is the town: München the point and München the
border. The importer takes the border and leaves the point out. But the
point is where mappers put what they know of the place -- München's has 95
names, its border 28, and "Monachium" is only on the point -- and it is not
always the same place: Schwentinental's seat is Raisdorf, a village inside
it, which left out is a village the data no longer has.

So the two are compared by name. The same name, or one that differs only in
how official it is -- "Singen" and "Singen (Hohentwiel)", "Schwarzenbach an
der Saale" and "Schwarzenbach a.d.Saale", "Kötzting" and "Bad Kötzting" --
is one place under two names, and both are kept. Any other is two places.
"""
import re
import unicodedata

# What follows the name proper in an official one: (Hohentwiel), /Vogtland,
# and where the place lies -- an der Saale, a.d.Saale, bei Coburg, b.Coburg,
# im Breisgau, ob der Tauber.
QUALIFIER = re.compile(
    r'\s*\(.*$'
    r'|\s*/.*$'
    r'|\s+(?:an|am|bei|im|in|ob|vor|auf|unter)\s.*$'
    r'|\s+[abiov]\.\s?(?:d\.)?\s?\S.*$',
    re.IGNORECASE)

UNDECOMPOSED = str.maketrans({'ß': 'ss', 'ł': 'l', 'ø': 'o', 'đ': 'd', 'æ': 'ae', 'œ': 'oe'})


def fold(name):
    name = unicodedata.normalize('NFKD', (name or '').casefold().translate(UNDECOMPOSED))
    name = ''.join(c for c in name if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w]+', ' ', name).split())


def core(name):
    """The name without what an official one adds after it."""
    return fold(QUALIFIER.sub('', name or '')) or fold(name)


def same_place(name, other):
    """
    Whether two names are one place's: the same, or one the other with more
    said before or after it. "Villingen" and "Villingen-Schwenningen" pass
    too -- no spelling tells a town named for two from a town and its
    qualifier -- which is what the importer did for every node before.
    """
    a, b = core(name), core(other)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return long.startswith(short + ' ') or long.endswith(' ' + short)


def differently_written(name, other):
    """The names are not the same text, case and accents aside: worth keeping both."""
    return bool(other) and fold(name) != fold(other)
