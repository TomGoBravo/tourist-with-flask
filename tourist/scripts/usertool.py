from flask.cli import AppGroup
import click
from tourist.models import tstore


usertool_cli = AppGroup('usertool')


@usertool_cli.command('list-users')
def list_users():
    for user in tstore.User.query.all():
        print(f'{user.id}: {user.username} {user.name} {user.email} Editor: {user.edit_granted}')
        for oa in user.oauth:
            print(f'    {oa.provider} {oa.created_at} {oa.provider_user_id} {oa.provider_user_login}')


@usertool_cli.command('set-user-edit')
@click.argument('user_id')
@click.argument('edit_granted')
def set_user_edit(user_id, edit_granted: str):
    user = tstore.User.query.filter_by(id=int(user_id)).one()
    new_edit_granted = edit_granted.lower() in ('true', 'yes', '1', 'set')

    if user.edit_granted == new_edit_granted:
        click.echo('No change')
    else:
        user.edit_granted = new_edit_granted
        click.echo(f'Saving {user.id} {user.username} {user.name} with Editor: {user.edit_granted}')
        tstore.db.session.add(user)
        tstore.db.session.commit()
