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
@click.option(
    "--safe",
    is_flag=True,
    help="可重复执行的安全模式：跳过已安装依赖、只补缺失/版本变更的默认配置并保留自定义项，输出每个插件的初始化报告。",
)
@click.option(
    "-n",
    "--name",
    "names",
    multiple=True,
    help="要初始化的插件名，可重复指定（如 -n poem -n oss）；不传则交互式询问。",
)
def plugin_init(safe, names):
    """
    initialize plugin
    """
    _plugin_init(names=list(names), safe=safe)


@plugin_cli.command("generate", with_appcontext=False)
def plugin_generate():
    """
    generate plugin
    """
    _plugin_generate()
