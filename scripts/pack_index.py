#!/usr/bin/env python3
"""
Which packs are published, and what built each one: an index of the
repository's pack releases, written as PACKS.md and packs.json.

Every pack is a release of its own, tagged <country>-<date>, and its release
says what built it twice over: the tag's target is the commit of the code
that made it, and the notes say which version is needed to load it. Spread
over a hundred releases, though, that answers nothing at a glance -- which
version built Poland, which packs a reader at 0.2.0 can load, which pack of
a country is the current one. This gathers it into one table.

    scripts/pack_index.py                # write PACKS.md and packs.json here
    scripts/pack_index.py --publish      # and put both on the `packs` release

`--publish` keeps one release, tagged `packs`, whose notes are the table and
whose assets are the two files; it is never marked latest. Needs the GitHub
CLI, `gh`, signed in.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = 'lechup/django-osm-locations'
INDEX_TAG = 'packs'

#: pl-2026-11-17, a state's us-ca-2026-10-24
PACK_TAG = re.compile(r'^(?P<token>[a-z]+(?:-[a-z]+)*)-(?P<date>\d{4}-\d{2}-\d{2})$')
BUILT_WITH = re.compile(r'built with (?:django-osm-locations )?v?(\d+(?:\.\d+)+)', re.I)
LOADS_WITH = re.compile(r'needs django-osm-locations v?(\d+(?:\.\d+)+) or later', re.I)


def gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True, check=True).stdout


def fetch_releases(repo):
    out = gh('api', 'repos/{}/releases?per_page=100'.format(repo), '--paginate', '--jq', '.[]')
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def fetch_versions(repo):
    """The commit each version tag points at: what a pack's target commit is matched to."""
    out = gh('api', 'repos/{}/tags'.format(repo), '--paginate', '--jq',
             '.[] | select(.name | test("^v[0-9]")) | "\\(.commit.sha) \\(.name)"')
    found = {}
    for line in out.splitlines():
        sha, name = line.split()
        found[sha] = name[1:]
    return found


def parse_release(release, versions):
    """One pack release as a row of the index, or None for anything else."""
    tag = release.get('tag_name') or ''
    match = PACK_TAG.match(tag)
    if not match or release.get('draft'):
        return None
    body = release.get('body') or ''
    target = release.get('target_commitish') or ''
    built = versions.get(target)
    if not built:
        found = BUILT_WITH.search(body)
        built = found.group(1) if found else None
    loads = LOADS_WITH.search(body)
    parts = [a for a in release.get('assets') or [] if '.tar.gz.part' in a.get('name', '')]
    title = release.get('name') or ''
    return {
        'pack': tag,
        'token': match.group('token'),
        'name': title.split(' · ')[0] if ' · ' in title else match.group('token'),
        'data_as_of': match.group('date'),
        'built_with': built,
        'commit': target if re.fullmatch(r'[0-9a-f]{40}', target) else None,
        'loads_with': loads.group(1) if loads else None,
        'size': sum(a.get('size', 0) for a in parts),
        'parts': len(parts),
        'published_at': release.get('published_at'),
        'url': release.get('html_url'),
    }


def build(releases, versions):
    """Every pack, the newest of each country marked current and the rest pointed at it."""
    packs = [p for p in (parse_release(r, versions) for r in releases) if p]
    newest = {}
    for pack in packs:
        if pack['data_as_of'] > newest.get(pack['token'], {'data_as_of': ''})['data_as_of']:
            newest[pack['token']] = pack
    for pack in packs:
        current = newest[pack['token']]
        pack['current'] = pack is current
        pack['replaced_by'] = None if pack is current else current['pack']
    return sorted(packs, key=lambda p: (p['token'], p['data_as_of']), reverse=False)


def human(size):
    for unit, scale in (('GB', 1e9), ('MB', 1e6)):
        if size >= scale:
            return '{:.1f} {}'.format(size / scale, unit)
    return '{} B'.format(size)


def markdown(packs, generated):
    current = sorted((p for p in packs if p['current']), key=lambda p: p['name'].lower())
    older = sorted((p for p in packs if not p['current']), key=lambda p: (p['token'], p['data_as_of']))
    lines = [
        '# Published packs',
        '',
        'One release per pack, tagged `<country>-<date>`: the date is how old its OpenStreetMap '
        'data is. **Built with** is the version of django-osm-locations that made the pack (the '
        "release's target commit); **loads with** is the oldest version that can load it. From "
        '0.2.4 a pack also carries both inside it, and `locations_import_snapshot` checks them.',
        '',
        'Generated {} from the releases; `packs.json` beside it holds the same as data.'.format(generated),
        '',
        '| Country | Pack | Data as of | Built with | Loads with | Size | Parts |',
        '|---|---|---|---|---|---:|---:|',
    ]
    for p in current:
        lines.append('| {} | [{}]({}) | {} | {} | {} | {} | {} |'.format(
            p['name'], p['pack'], p['url'], p['data_as_of'], p['built_with'] or '?',
            '{} or later'.format(p['loads_with']) if p['loads_with'] else '?',
            human(p['size']), p['parts']))
    if older:
        lines += ['', '## Replaced', '',
                  'Still downloadable; a newer pack of the same country is above.', '',
                  '| Pack | Built with | Loads with | Replaced by |', '|---|---|---|---|']
        for p in older:
            lines.append('| [{}]({}) | {} | {} | {} |'.format(
                p['pack'], p['url'], p['built_with'] or '?',
                '{} or later'.format(p['loads_with']) if p['loads_with'] else '?', p['replaced_by']))
    return '\n'.join(lines) + '\n'


def publish(repo, md_path, json_path):
    exists = subprocess.run(['gh', 'release', 'view', INDEX_TAG, '-R', repo],
                            capture_output=True).returncode == 0
    if exists:
        gh('release', 'edit', INDEX_TAG, '-R', repo, '--notes-file', md_path)
    else:
        gh('release', 'create', INDEX_TAG, '-R', repo, '--title', 'Pack index',
           '--notes-file', md_path, '--latest=false')
    gh('release', 'upload', INDEX_TAG, '-R', repo, '--clobber', md_path, json_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--repo', default=REPO)
    parser.add_argument('--out-dir', default='.')
    parser.add_argument('--publish', action='store_true')
    options = parser.parse_args(argv)

    packs = build(fetch_releases(options.repo), fetch_versions(options.repo))
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    md_path = os.path.join(options.out_dir, 'PACKS.md')
    json_path = os.path.join(options.out_dir, 'packs.json')
    with open(md_path, 'w') as f:
        f.write(markdown(packs, generated))
    with open(json_path, 'w') as f:
        json.dump({'generated': generated, 'packs': packs}, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('{} packs, {} current: {}, {}'.format(
        len(packs), sum(p['current'] for p in packs), md_path, json_path))
    if options.publish:
        publish(options.repo, md_path, json_path)
        print('published on the {} release'.format(INDEX_TAG))
    return 0


if __name__ == '__main__':
    sys.exit(main())
