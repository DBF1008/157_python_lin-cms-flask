"""
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import uuid

from . import app, fixtureFunc, get_token


def _auth():
    return {"Authorization": "Bearer " + get_token()}


def _uid():
    return uuid.uuid4().hex[:6]


def _create_group(c, name=None, info="test", permission_ids=None):
    """创建分组，返回分组 JSON（含 id）"""
    if name is None:
        name = "g_" + _uid()
    if permission_ids is None:
        permission_ids = []
    rv = c.post(
        "/cms/admin/group",
        headers=_auth(),
        json={"name": name, "info": info, "permission_ids": permission_ids},
    )
    assert rv.status_code == 200, rv.get_json()
    # 查询刚创建的分组
    rv2 = c.get("/cms/admin/group/all", headers=_auth())
    assert rv2.status_code == 200
    for g in rv2.get_json():
        if g["name"] == name:
            return g
    raise RuntimeError(f"group {name} not found after creation")


def _create_user(c, group_ids, username=None, email=None):
    """创建用户，返回用户 JSON"""
    if username is None:
        username = "u_" + _uid()
    if email is None:
        email = f"{username}@test.com"
    rv = c.post(
        "/cms/user/register",
        headers=_auth(),
        json={
            "username": username,
            "password": "123456",
            "confirm_password": "123456",
            "email": email,
            "group_ids": group_ids,
        },
    )
    assert rv.status_code == 200, rv.get_json()
    # 查询刚创建的用户
    rv2 = c.get("/cms/admin/users", headers=_auth(), query_string={"page": 0, "count": 15})
    for u in rv2.get_json()["items"]:
        if u["username"] == username:
            return u
    raise RuntimeError(f"user {username} not found after creation")


def _get_guest_group(c):
    rv = c.get("/cms/admin/group/all", headers=_auth())
    for g in rv.get_json():
        if g["name"] == "Guest":
            return g
    raise RuntimeError("Guest group not found")


def _cleanup_group(c, gid):
    """尝试删除分组（尽力清理）"""
    try:
        c.delete(f"/cms/admin/group/{gid}", headers=_auth())
    except Exception:
        pass


def _cleanup_user(c, uid):
    """尝试删除用户（尽力清理）"""
    try:
        c.delete(f"/cms/admin/user/{uid}", headers=_auth())
    except Exception:
        pass


# ─── 原有冒烟测试 ────────────────────────────────────────────


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


# ─── 分组删除安全迁移回归测试 ─────────────────────────────────


def test_delete_group_no_users(fixtureFunc):
    """分组下无用户时正常删除（回归原逻辑）"""
    with app.test_client() as c:
        group = _create_group(c)
        gid = group["id"]

        rv = c.delete(f"/cms/admin/group/{gid}", headers=_auth())
        assert rv.status_code == 200
        assert "成功" in rv.get_json().get("message", "")


def test_delete_group_without_migrate_blocked(fixtureFunc):
    """分组下有用户、不传 migrate_to_group_id → 401 Forbidden"""
    with app.test_client() as c:
        group = _create_group(c)
        gid = group["id"]
        user = _create_user(c, group_ids=[gid])
        uid = user["id"]

        try:
            rv = c.delete(f"/cms/admin/group/{gid}", headers=_auth())
            assert rv.status_code == 401
        finally:
            _cleanup_user(c, uid)
            _cleanup_group(c, gid)


def test_delete_group_migrate_to_guest(fixtureFunc):
    """传入 Guest 分组 ID → 用户迁移到 Guest、分组被删、权限关联清理"""
    with app.test_client() as c:
        group = _create_group(c)
        gid = group["id"]
        user = _create_user(c, group_ids=[gid])
        uid = user["id"]
        guest = _get_guest_group(c)

        try:
            rv = c.delete(
                f"/cms/admin/group/{gid}",
                headers=_auth(),
                query_string={"migrate_to_group_id": guest["id"]},
            )
            assert rv.status_code == 200, rv.get_json()

            # 验证用户现在属于 Guest 分组
            rv2 = c.get("/cms/admin/users", headers=_auth(), query_string={"page": 0, "count": 15})
            target_user = None
            for u in rv2.get_json()["items"]:
                if u["id"] == uid:
                    target_user = u
                    break
            assert target_user is not None
            guest_ids = [g["id"] for g in target_user["groups"]]
            assert guest["id"] in guest_ids

            # 验证原分组已被删除
            rv3 = c.get(f"/cms/admin/group/{gid}", headers=_auth())
            assert rv3.status_code == 404
        finally:
            _cleanup_user(c, uid)


def test_delete_group_migrate_to_custom_group(fixtureFunc):
    """传入另一个自定义分组 ID → 用户迁移成功"""
    with app.test_client() as c:
        src_group = _create_group(c, name="src_" + _uid())
        dst_group = _create_group(c, name="dst_" + _uid())
        src_gid = src_group["id"]
        dst_gid = dst_group["id"]
        user = _create_user(c, group_ids=[src_gid])
        uid = user["id"]

        try:
            rv = c.delete(
                f"/cms/admin/group/{src_gid}",
                headers=_auth(),
                query_string={"migrate_to_group_id": dst_gid},
            )
            assert rv.status_code == 200, rv.get_json()

            # 验证用户现在属于目标分组
            rv2 = c.get("/cms/admin/users", headers=_auth(), query_string={"page": 0, "count": 15})
            target_user = None
            for u in rv2.get_json()["items"]:
                if u["id"] == uid:
                    target_user = u
                    break
            assert target_user is not None
            group_ids = [g["id"] for g in target_user["groups"]]
            assert dst_gid in group_ids
        finally:
            _cleanup_user(c, uid)
            _cleanup_group(c, dst_gid)


def test_delete_group_migrate_to_root_forbidden(fixtureFunc):
    """传入 Root 分组 ID → 401 Forbidden"""
    with app.test_client() as c:
        group = _create_group(c)
        gid = group["id"]
        user = _create_user(c, group_ids=[gid])
        uid = user["id"]

        # 获取 Root 分组 ID
        rv_all = c.get("/cms/admin/users", headers=_auth(), query_string={"page": 0, "count": 1})
        # Root 用户一定存在，通过管理员 API 获取 Root group id
        # Root 分组不在 /cms/admin/group/all 中返回，我们从用户关联表反推
        from app.lin import GroupLevelEnum, manager

        root_group = manager.group_model.get(level=GroupLevelEnum.ROOT.value)
        root_gid = root_group.id

        try:
            rv = c.delete(
                f"/cms/admin/group/{gid}",
                headers=_auth(),
                query_string={"migrate_to_group_id": root_gid},
            )
            assert rv.status_code == 401
        finally:
            _cleanup_user(c, uid)
            _cleanup_group(c, gid)


def test_delete_group_migrate_to_nonexistent(fixtureFunc):
    """传入不存在的分组 ID → 404"""
    with app.test_client() as c:
        group = _create_group(c)
        gid = group["id"]
        user = _create_user(c, group_ids=[gid])
        uid = user["id"]

        try:
            rv = c.delete(
                f"/cms/admin/group/{gid}",
                headers=_auth(),
                query_string={"migrate_to_group_id": 999999},
            )
            assert rv.status_code == 404
        finally:
            _cleanup_user(c, uid)
            _cleanup_group(c, gid)


def test_delete_group_migrate_to_self(fixtureFunc):
    """目标分组与待删除分组相同 → 400 ParameterError"""
    with app.test_client() as c:
        group = _create_group(c)
        gid = group["id"]
        user = _create_user(c, group_ids=[gid])
        uid = user["id"]

        try:
            rv = c.delete(
                f"/cms/admin/group/{gid}",
                headers=_auth(),
                query_string={"migrate_to_group_id": gid},
            )
            assert rv.status_code == 400
        finally:
            _cleanup_user(c, uid)
            _cleanup_group(c, gid)


def test_delete_group_migrate_skip_existing(fixtureFunc):
    """用户已在目标分组中 → 不产生重复关联"""
    with app.test_client() as c:
        src_group = _create_group(c, name="src_" + _uid())
        dst_group = _create_group(c, name="dst_" + _uid())
        src_gid = src_group["id"]
        dst_gid = dst_group["id"]
        # 用户同时在 src 和 dst 两个分组中
        user = _create_user(c, group_ids=[src_gid, dst_gid])
        uid = user["id"]

        try:
            rv = c.delete(
                f"/cms/admin/group/{src_gid}",
                headers=_auth(),
                query_string={"migrate_to_group_id": dst_gid},
            )
            assert rv.status_code == 200, rv.get_json()

            # 验证用户仍属于目标分组，且只有一条关联记录
            rv2 = c.get("/cms/admin/users", headers=_auth(), query_string={"page": 0, "count": 15})
            target_user = None
            for u in rv2.get_json()["items"]:
                if u["id"] == uid:
                    target_user = u
                    break
            assert target_user is not None
            # 用户应只属于 dst 分组（src 已删除）
            group_ids = [g["id"] for g in target_user["groups"]]
            assert dst_gid in group_ids
            assert src_gid not in group_ids

            # 验证没有重复的 UserGroup 记录
            from app.lin import db, manager

            dup_count = (
                db.session.query(manager.user_group_model)
                .filter(manager.user_group_model.user_id == uid, manager.user_group_model.group_id == dst_gid)
                .count()
            )
            assert dup_count == 1, f"Expected 1 UserGroup record, got {dup_count}"
        finally:
            _cleanup_user(c, uid)
            _cleanup_group(c, dst_gid)


def test_delete_root_or_guest_forbidden(fixtureFunc):
    """删除 Root/Guest 分组 → 401（回归保护语义）"""
    with app.test_client() as c:
        guest = _get_guest_group(c)

        # 删除 Guest
        rv = c.delete(f"/cms/admin/group/{guest['id']}", headers=_auth())
        assert rv.status_code == 401

        # 删除 Root（从 manager 获取）
        from app.lin import GroupLevelEnum, manager

        root_group = manager.group_model.get(level=GroupLevelEnum.ROOT.value)
        rv2 = c.delete(f"/cms/admin/group/{root_group.id}", headers=_auth())
        assert rv2.status_code == 401
