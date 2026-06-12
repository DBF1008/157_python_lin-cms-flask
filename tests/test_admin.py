"""
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import uuid

from app.api.cms.model.group import Group
from app.api.cms.model.group_permission import GroupPermission
from app.api.cms.model.user import User
from app.api.cms.model.user_group import UserGroup
from app.lin import GroupLevelEnum

from . import app, fixtureFunc, get_token


def test_permission(fixtureFunc):
    with app.test_client() as c:
        rv = c.get(
            "/cms/admin/permission",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200


def test_get_root_users(fixtureFunc):
    with app.test_client() as c:
        rv = c.get("/cms/admin/users", headers={"Authorization": "Bearer " + get_token()})
        assert rv.status_code == 200


# ---------------------------------------------------------------------------
# 分组删除安全迁移能力的回归测试
# ---------------------------------------------------------------------------


def _auth_headers():
    return {"Authorization": "Bearer " + get_token()}


def _unique_group_name(prefix):
    return "{0}-{1}".format(prefix, uuid.uuid4().hex[:8])


def _unique_username():
    # 用户名约束：仅字母、数字、下划线，长度 2-10
    return "u" + uuid.uuid4().hex[:8]


def _create_group(c, name, permission_ids=None):
    rv = c.post(
        "/cms/admin/group",
        headers=_auth_headers(),
        json={"name": name, "info": name, "permission_ids": permission_ids or []},
    )
    assert rv.status_code == 200, rv.get_json()
    return _group_id_by_name(c, name)


def _group_id_by_name(c, name):
    rv = c.get("/cms/admin/group/all", headers=_auth_headers())
    assert rv.status_code == 200, rv.get_json()
    for group in rv.get_json():
        if group["name"] == name:
            return group["id"]
    raise AssertionError("group not found: {0}".format(name))


def _register_user(c, username, group_ids):
    rv = c.post(
        "/cms/user/register",
        headers=_auth_headers(),
        json={
            "username": username,
            "password": "123456",
            "confirm_password": "123456",
            "group_ids": group_ids,
        },
    )
    assert rv.status_code == 200, rv.get_json()
    with app.app_context():
        user = User.get(username=username)
        return user.id if user else None


def _delete_user(c, user_id):
    if user_id is not None:
        c.delete("/cms/admin/user/{0}".format(user_id), headers=_auth_headers())


def _usernames_in_group(c, group_id):
    rv = c.get("/cms/admin/users?group_id={0}".format(group_id), headers=_auth_headers())
    assert rv.status_code == 200, rv.get_json()
    return {item["username"] for item in rv.get_json()["items"]}


def _root_group_id():
    with app.app_context():
        return Group.get(level=GroupLevelEnum.ROOT.value).id


def _user_in_group(user_id, group_id):
    with app.app_context():
        return UserGroup.query.filter_by(user_id=user_id, group_id=group_id).count() > 0


def _group_is_soft_deleted(group_id):
    with app.app_context():
        group = Group.query.filter_by(id=group_id).first()
        return group is not None and bool(group.is_deleted)


def _count_group_permission(group_id):
    with app.app_context():
        return GroupPermission.query.filter_by(group_id=group_id).count()


def _count_user_group(group_id):
    with app.app_context():
        return UserGroup.query.filter_by(group_id=group_id).count()


def test_delete_group_migrate_users_to_specified_group(fixtureFunc):
    """删除分组时把受影响用户迁移到指定的其他分组，再清理分组与权限关联。"""
    with app.test_client() as c:
        source_id = _create_group(c, _unique_group_name("src"), permission_ids=[1])
        target_id = _create_group(c, _unique_group_name("dst"))
        username = _unique_username()
        user_id = _register_user(c, username, [source_id])
        try:
            assert username in _usernames_in_group(c, source_id)

            rv = c.delete(
                "/cms/admin/group/{0}?transfer_group_id={1}".format(source_id, target_id),
                headers=_auth_headers(),
            )
            assert rv.status_code == 200, rv.get_json()

            # 用户已迁移到目标分组，且不再属于源分组（API 视角）
            assert username in _usernames_in_group(c, target_id)
            assert username not in _usernames_in_group(c, source_id)
            # 关联关系（DB 视角）
            assert _user_in_group(user_id, target_id)
            assert not _user_in_group(user_id, source_id)
            # 源分组被软删除，权限关联与用户关联均被清理
            assert _group_is_soft_deleted(source_id)
            assert _count_group_permission(source_id) == 0
            assert _count_user_group(source_id) == 0
        finally:
            _delete_user(c, user_id)


def test_delete_group_migrate_users_to_guest(fixtureFunc):
    """删除分组时把受影响用户迁移到 Guest 分组。"""
    with app.test_client() as c:
        guest_id = _group_id_by_name(c, "Guest")
        source_id = _create_group(c, _unique_group_name("src-guest"))
        username = _unique_username()
        user_id = _register_user(c, username, [source_id])
        try:
            rv = c.delete(
                "/cms/admin/group/{0}?transfer_group_id={1}".format(source_id, guest_id),
                headers=_auth_headers(),
            )
            assert rv.status_code == 200, rv.get_json()
            assert _user_in_group(user_id, guest_id)
            assert _group_is_soft_deleted(source_id)
            assert _count_user_group(source_id) == 0
        finally:
            _delete_user(c, user_id)


def test_delete_group_migrate_dedup_when_user_already_in_target(fixtureFunc):
    """用户同时属于源分组和目标分组时，迁移不产生重复关联。"""
    with app.test_client() as c:
        source_id = _create_group(c, _unique_group_name("src-dup"))
        target_id = _create_group(c, _unique_group_name("dst-dup"))
        username = _unique_username()
        # 用户同时属于源、目标两个分组
        user_id = _register_user(c, username, [source_id, target_id])
        try:
            rv = c.delete(
                "/cms/admin/group/{0}?transfer_group_id={1}".format(source_id, target_id),
                headers=_auth_headers(),
            )
            assert rv.status_code == 200, rv.get_json()
            # 目标分组中该用户仅有一条关联记录
            with app.app_context():
                relation_count = UserGroup.query.filter_by(user_id=user_id, group_id=target_id).count()
            assert relation_count == 1
            assert _count_user_group(source_id) == 0
        finally:
            _delete_user(c, user_id)


def test_delete_group_blocked_when_users_exist_without_transfer(fixtureFunc):
    """不指定迁移目标且分组下存在用户时，保持原有保护：拒绝删除。"""
    with app.test_client() as c:
        source_id = _create_group(c, _unique_group_name("blk"))
        username = _unique_username()
        user_id = _register_user(c, username, [source_id])
        try:
            rv = c.delete("/cms/admin/group/{0}".format(source_id), headers=_auth_headers())
            assert rv.status_code == 401
            assert rv.get_json()["code"] == 10070
            # 分组未被删除，用户仍在源分组
            assert not _group_is_soft_deleted(source_id)
            assert _user_in_group(user_id, source_id)
        finally:
            _delete_user(c, user_id)


def test_delete_group_rejects_invalid_transfer_target(fixtureFunc):
    """迁移目标分组非法（不存在 / 为 Root 分组）时拒绝并保持原状。"""
    with app.test_client() as c:
        source_id = _create_group(c, _unique_group_name("inv"))
        username = _unique_username()
        user_id = _register_user(c, username, [source_id])
        try:
            # 目标分组不存在
            rv = c.delete(
                "/cms/admin/group/{0}?transfer_group_id=999999".format(source_id),
                headers=_auth_headers(),
            )
            assert rv.status_code == 400
            assert rv.get_json()["code"] == 10030

            # 目标分组为超级管理员（Root）分组：保留 Root 保护语义
            rv = c.delete(
                "/cms/admin/group/{0}?transfer_group_id={1}".format(source_id, _root_group_id()),
                headers=_auth_headers(),
            )
            assert rv.status_code == 401
            assert rv.get_json()["code"] == 10070

            # 失败后源分组仍存在、用户仍在源分组
            assert not _group_is_soft_deleted(source_id)
            assert _user_in_group(user_id, source_id)
        finally:
            _delete_user(c, user_id)


def test_delete_group_protects_root_and_guest(fixtureFunc):
    """Root / Guest 分组始终不可删除。"""
    with app.test_client() as c:
        guest_id = _group_id_by_name(c, "Guest")
        for protected_id in (guest_id, _root_group_id()):
            rv = c.delete("/cms/admin/group/{0}".format(protected_id), headers=_auth_headers())
            assert rv.status_code == 401
            assert rv.get_json()["code"] == 10070
        # 保护分组仍然存在
        assert not _group_is_soft_deleted(guest_id)
        assert not _group_is_soft_deleted(_root_group_id())


def test_delete_empty_group_still_succeeds(fixtureFunc):
    """不带用户的分组仍可直接删除（向后兼容）。"""
    with app.test_client() as c:
        group_id = _create_group(c, _unique_group_name("empty"), permission_ids=[1])
        rv = c.delete("/cms/admin/group/{0}".format(group_id), headers=_auth_headers())
        assert rv.status_code == 200, rv.get_json()
        assert _group_is_soft_deleted(group_id)
        assert _count_group_permission(group_id) == 0
