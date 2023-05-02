import sqlite3
import click
import re

@click.group()
def cli():
    pass

@cli.command()
@click.argument('db_file_path')
def add_comment_spam_fields(db_file_path: str):
    con = sqlite3.connect(db_file_path)
    with con:
        con.execute("ALTER TABLE place_comment ADD remote_addr VARCHAR")
        con.execute("ALTER TABLE place_comment ADD user_agent VARCHAR")
        con.execute("ALTER TABLE place_comment ADD akismet_spam_status INTEGER")
    with con:
        list_ip_id = []
        for id_, source in con.execute("SELECT id, source FROM place_comment WHERE source LIKE 'Web visitor %'"):
            ip_address = re.match(r'Web visitor at (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', source).group(1)
            list_ip_id.append((ip_address, id_))
        con.executemany("UPDATE place_comment SET remote_addr = ? WHERE id = ?", list_ip_id)
    con.close()


@cli.command()
@click.argument('db_file_path')
def add_source_place_id(db_file_path: str):
    con = sqlite3.connect(db_file_path)
    with con:
        con.execute("ALTER TABLE source ADD place_id INTEGER")
    con.close()


if __name__ == '__main__':
    cli()