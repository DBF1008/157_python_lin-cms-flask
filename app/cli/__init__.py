import click
from flask.cli import AppGroup

from .db import fake as _db_fake
from .db import init as _db_init
from .plugin import generate as _plugin_generate
from .plugin import init as _plugin_init

db_cli = AppGroup("db")
plugin_cli = AppGroup("plugin")


@db_cli.command("init")
@click.option("--force", is_flag=True, help="Create after drop.")
def db_init(force):
    """
    initialize the database.
    """
    if force:
        click.confirm("此操作将清空数据，是否继续?", abort=True)
    _db_init(force)
    click.echo("数据库初始化成功")


@db_cli.command("fake")
def db_fake():
    """
    fake the db data.
    """
    _db_fake()
    click.echo("fake数据添加成功")


@plugin_cli.command("init", with_appcontext=False)
@click.argument("names", nargs=-1, required=False)
@click.option("--safe", is_flag=True, help="安全模式：可重复执行，保留已有配置，跳过已满足的依赖")
@click.option("--all", "use_all", is_flag=True, help="初始化所有插件（等同于输入 *）")
def plugin_init(names, safe, use_all):
    """initialize plugin(s)."""
    if use_all:
        plugin_name = "*"
    elif names:
        plugin_name = " ".join(names)
    else:
        plugin_name = None
    _plugin_init(plugin_name, safe=safe)


@plugin_cli.command("generate", with_appcontext=False)
def plugin_generate():
    """
    generate plugin
    """
    _plugin_generate()
