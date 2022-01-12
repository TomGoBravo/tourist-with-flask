import re
from typing import Optional

import attr
import click
from flask.cli import AppGroup

from tourist.models import sqlalchemy

batchtool_cli = AppGroup('batchtool')


@attr.s(auto_attribs=True, slots=True)
class ClubPoolLink:
    club: sqlalchemy.Club
    target_short_name: str
    title: Optional[str]

    def __str__(self):
        return f"Link in {self.club.short_name} to {self.target_short_name}"


PAGE_LINK_RE = r'\[([^]]+)]\(/tourist/page/([^)]+)\)'


@batchtool_cli.command('club-pool-links')
def club_pool_links():
    links = []
    for club in sqlalchemy.Club.query.all():
        for link_title, link_target in re.findall(PAGE_LINK_RE, club.markdown):
            links.append(ClubPoolLink(club, link_target, link_title))
        for link_target in re.findall(r'\[\[(\w+)]]', club.markdown):
            links.append(ClubPoolLink(club, link_target, None))

    pools = {}
    for pool in sqlalchemy.Pool.query.all():
        pools[pool.short_name] = pool

    good_links = []
    not_found_links = []
    diff_place_links = []
    diff_title_links = []
    for link in links:
        if link.target_short_name not in pools:
            not_found_links.append(link)
            continue
        target_pool = pools[link.target_short_name]
        if target_pool.parent.short_name != link.club.parent.short_name:
            diff_place_links.append(link)
            continue
        if link.title and target_pool.name != link.title:
            # Output while target_pool is set
            click.echo(f'{link}: title {target_pool.name} != {link.title}')
            diff_title_links.append(link)
            continue
        good_links.append(link)
    click.echo('Link target short_name not found:')
    for link in not_found_links:
        click.echo(f'    {link}')
    click.echo('Link target in different place:')
    for link in diff_place_links:
        click.echo(f'    {link}')
    click.echo(f'{len(good_links)} good links')


@batchtool_cli.command('replace-club-pool-links')
@click.option('--write', is_flag=True)
def replace_club_pool_links(write):
    replacements = 0
    modified_clubs = []
    for club in sqlalchemy.Club.query.all():
        new_markdown, sub_count = re.subn(PAGE_LINK_RE, r'[[\2]]', club.markdown)
        if sub_count > 0:
            club.markdown = new_markdown
            sqlalchemy.db.session.add(club)
            modified_clubs.append(club.short_name)
        replacements += sub_count

    click.echo(f'Replacing {replacements} links in {", ".join(modified_clubs)}')

    if write:
        click.echo('Committing changes')
        sqlalchemy.db.session.commit()
    else:
        click.echo('Run with --write to commit changes')

