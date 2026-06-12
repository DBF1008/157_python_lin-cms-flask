"""
内置分组(Root / Guest)解析的回归测试。
~~~~~~~~~

历史缺陷：代码在多处直接把 ``GroupLevelEnum`` 的 level 枚举值(ROOT=1, GUEST=2)
当作真实的 group id 使用。只要数据库初始化(插入)顺序发生变化，使得 Root 分组的
真实主键 id 不再等于 1、Guest 分组的真实主键 id 不再等于 2，就会出现：

* 管理员识别错乱：超级管理员被判定为非管理员；恰好占用 id==1 的普通分组成员被误判为管理员。
* 注册默认分组错乱：未指定分组的新用户被塞进“恰好 id==2 的分组”（可能正是 Root！），而不是真正的 Guest。
* 个人权限接口 ``/cms/user/permissions`` 返回的 ``admin`` 字段错误。

本测试通过“打乱内置分组的创建顺序”来稳定复现上述场景：先建一个 USER 级占位分组(占用 id==1)，
再建 Root(得到 id==2)，最后建 Guest(得到 id==3)。在这种布局下：

* Root.id (2) != GroupLevelEnum.ROOT.value (1)
* Guest.id (3) != GroupLevelEnum.GUEST.value (2)
* 占位分组 id (1) == GroupLevelEnum.ROOT.value (1)  —— 旧逻辑会把它的成员误判为管理员

修复后所有判断都按“真实分组记录”解析 Root / Guest，断言全部通过；若回退到旧逻辑，断言会失败。
"""

import atexit
import os
import tempfile
from types import SimpleNamespace

import pytest

import app.config.base as base_config
from app.lin import GroupLevelEnum, db, get_tokens, manager

# ---------------------------------------------------------------------------
# 构造一个与共享测试 app 隔离的、绑定到独立临时 sqlite 数据库的 app。
# 之所以隔离，是因为本测试需要“干净且顺序可控”的内置分组数据，
# 不能依赖也不能污染开发库里 `flask db init` 生成的 Root(id=1)/Guest(id=2)。
# ---------------------------------------------------------------------------
_tmp_fd, _TMP_DB_PATH = tempfile.mkstemp(suffix="_group_resolution.db")
os.close(_tmp_fd)
atexit.register(lambda: os.path.exists(_TMP_DB_PATH) and os.remove(_TMP_DB_PATH))


def _build_isolated_app():
    from app import create_app
    from app.api.cms.model.group import Group
    from app.api.cms.model.group_permission import GroupPermission
    from app.api.cms.model.permission import Permission
    from app.api.cms.model.user import User
    from app.api.cms.model.user_group import UserGroup
    from app.api.cms.model.user_identity import UserIdentity

    # create_app 通过 app.config.from_object 读取 BaseConfig 的类属性来确定数据库地址，
    # 这里临时覆盖该类属性，让隔离 app 指向独立的临时库，随后立即恢复，避免影响其它 app。
    original_uri = base_config.BaseConfig.SQLALCHEMY_DATABASE_URI
    base_config.BaseConfig.SQLALCHEMY_DATABASE_URI = "sqlite:///" + _TMP_DB_PATH
    try:
        isolated_app = create_app(
            group_model=Group,
            user_model=User,
            group_permission_model=GroupPermission,
            permission_model=Permission,
            identity_model=UserIdentity,
            user_group_model=UserGroup,
        )
    finally:
        base_config.BaseConfig.SQLALCHEMY_DATABASE_URI = original_uri
    return isolated_app


iso_app = _build_isolated_app()


def _add_group(name, info, level):
    group = manager.group_model()
    group.name = name
    group.info = info
    group.level = level
    db.session.add(group)
    db.session.flush()  # 立即拿到自增 id
    return group


def _add_user(username, group_id):
    user = manager.user_model()
    user.username = username
    db.session.add(user)
    db.session.flush()  # 先拿到 user.id，password setter 需要它
    user.password = "123456"
    user_group = manager.user_group_model()
    user_group.user_id = user.id
    user_group.group_id = group_id
    db.session.add(user_group)
    db.session.flush()
    return user


@pytest.fixture(scope="module")
def env():
    """以打乱的顺序初始化内置分组与用户，返回各真实 id 及管理员令牌。"""
    with iso_app.app_context():
        # 故意打乱创建顺序：占位(USER) -> Root -> Guest
        filler_group = _add_group("Filler", "占位普通分组", GroupLevelEnum.USER.value)
        root_group = _add_group("Root", "超级用户组", GroupLevelEnum.ROOT.value)
        guest_group = _add_group("Guest", "游客组", GroupLevelEnum.GUEST.value)

        root_user = _add_user("root", root_group.id)
        filler_user = _add_user("filler", filler_group.id)
        db.session.commit()

        data = SimpleNamespace(
            filler_group_id=filler_group.id,
            root_group_id=root_group.id,
            guest_group_id=guest_group.id,
            root_user_id=root_user.id,
            filler_user_id=filler_user.id,
        )
        # 令牌签发需要 app 上下文
        data.admin_token, _ = get_tokens(root_user)

    return data


def test_builtin_group_ids_are_shifted(env):
    """前置条件自检：确认打乱顺序确实让真实 id 偏离了 level 枚举值，从而复现缺陷场景。"""
    assert env.root_group_id != GroupLevelEnum.ROOT.value, "Root 的真实 id 未发生偏移，无法复现缺陷"
    assert env.guest_group_id != GroupLevelEnum.GUEST.value, "Guest 的真实 id 未发生偏移，无法复现缺陷"
    # 占位分组占据了 id==ROOT.value，旧逻辑会据此误判其成员为管理员
    assert env.filler_group_id == GroupLevelEnum.ROOT.value


def test_resolver_returns_real_group_ids(env):
    """Root / Guest 必须按真实分组记录解析，而不是返回 level 枚举值。"""
    with iso_app.app_context():
        assert manager.group_model.get_root_group_id() == env.root_group_id
        assert manager.group_model.get_guest_group_id() == env.guest_group_id


def test_is_admin_uses_real_root_group(env):
    """管理员识别必须基于真实 Root 分组 id，不能直接用 level 枚举值比较。"""
    with iso_app.app_context():
        root_user = manager.user_model.get(id=env.root_user_id)
        filler_user = manager.user_model.get(id=env.filler_user_id)

        # 超级管理员(在真实 Root 分组中)应被识别为管理员
        assert root_user.is_admin is True
        # 占位分组成员(其分组 id 恰好等于 ROOT.value)绝不能被误判为管理员
        assert filler_user.is_admin is False


def test_register_defaults_to_real_guest_group(env):
    """未指定分组的注册用户应落入真实 Guest 分组，而非 id 恰为 GUEST.value 的分组。"""
    client = iso_app.test_client()
    rv = client.post(
        "/cms/user/register",
        headers={"Authorization": "Bearer " + env.admin_token, "Content-Type": "application/json"},
        json={
            "username": "newbie",
            "password": "123456",
            "confirm_password": "123456",
            "group_ids": [],
        },
    )
    assert rv.status_code == 200, rv.get_json()

    with iso_app.app_context():
        newbie = manager.user_model.get(username="newbie")
        assert newbie is not None
        assigned_group_ids = [ug.group_id for ug in manager.user_group_model.get(user_id=newbie.id, one=False)]
        # 必须是真实 Guest 分组 id(3)，而不是 GUEST.value(2)——后者在本布局下其实是 Root 分组
        assert assigned_group_ids == [env.guest_group_id]
        assert env.guest_group_id != GroupLevelEnum.GUEST.value
        # 游客不是管理员
        assert newbie.is_admin is False


def test_permissions_endpoint_reports_admin(env):
    """个人权限接口对真实管理员应返回 admin=True。"""
    client = iso_app.test_client()
    rv = client.get(
        "/cms/user/permissions",
        headers={"Authorization": "Bearer " + env.admin_token},
    )
    assert rv.status_code == 200
    # 该接口未声明响应 schema，auto_response 返回的 content-type 不是 application/json，
    # 这是与本次缺陷无关的既有行为，因此强制按 JSON 解析响应体。
    body = rv.get_json(force=True)
    assert body is not None
    assert body.get("admin") is True
