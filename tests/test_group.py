"""
Regression tests for the group identification chain.

Verifies that Root and Guest built-in groups are resolved by their ``level``
field (not by assuming group_id == enum value), covering:
- User.is_admin property
- Registration default group assignment
- /cms/user/permissions admin flag
- @admin_required enforcement

:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import json

from . import app, fixtureFunc, get_token

from app.lin import GroupLevelEnum, db, manager


# ---------------------------------------------------------------------------
# User.is_admin
# ---------------------------------------------------------------------------

def test_root_user_is_admin(fixtureFunc):
    """Root 用户的 is_admin 应通过 group.level 判定为 True"""
    with app.app_context():
        root_user = manager.user_model.query.filter_by(username="root").first()
        assert root_user is not None
        assert root_user.is_admin is True

        groups = manager.group_model.select_by_user_id(root_user.id)
        assert len(groups) > 0
        assert any(g.level == GroupLevelEnum.ROOT.value for g in groups)


# ---------------------------------------------------------------------------
# /cms/user/permissions — admin flag
# ---------------------------------------------------------------------------

def test_permissions_endpoint_admin_flag(fixtureFunc):
    """GET /cms/user/permissions 对 root 用户应返回 admin: true"""
    with app.test_client() as c:
        rv = c.get(
            "/cms/user/permissions",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["admin"] is True


# ---------------------------------------------------------------------------
# Registration default group
# ---------------------------------------------------------------------------

def test_register_default_group_is_guest(fixtureFunc):
    """注册不传 group_ids 时，用户应被分配到 level == GUEST 的真实分组"""
    import uuid

    username = "g" + uuid.uuid4().hex[:7]

    with app.test_client() as c:
        rv = c.post(
            "/cms/user/register",
            headers={
                "Authorization": "Bearer " + get_token(),
                "Content-Type": "application/json",
            },
            json={
                "username": username,
                "password": "123456",
                "confirm_password": "123456",
                "group_ids": [],
            },
        )
        assert rv.status_code == 200

    with app.app_context():
        new_user = manager.user_model.query.filter_by(username=username).first()
        assert new_user is not None

        groups = manager.group_model.select_by_user_id(new_user.id)
        assert len(groups) > 0
        assert groups[0].level == GroupLevelEnum.GUEST.value

        assert new_user.is_admin is False


# ---------------------------------------------------------------------------
# Registered (non-admin) user: admin flag in permissions
# ---------------------------------------------------------------------------

def test_registered_user_permissions_admin_false(fixtureFunc):
    """非管理员用户的 /cms/user/permissions 应返回 admin: false"""
    import uuid

    username = "p" + uuid.uuid4().hex[:7]

    with app.test_client() as c:
        # Register a non-admin user (default Guest group)
        rv = c.post(
            "/cms/user/register",
            headers={
                "Authorization": "Bearer " + get_token(),
                "Content-Type": "application/json",
            },
            json={
                "username": username,
                "password": "123456",
                "confirm_password": "123456",
                "group_ids": [],
            },
        )
        assert rv.status_code == 200

        # Login as the new user
        rv = c.post(
            "/cms/user/login",
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": "123456"},
        )
        assert rv.status_code == 200
        user_token = rv.get_json()["access_token"]

        # Check permissions endpoint — admin must be false
        rv = c.get(
            "/cms/user/permissions",
            headers={"Authorization": "Bearer " + user_token},
        )
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["admin"] is False


# ---------------------------------------------------------------------------
# @admin_required enforcement
# ---------------------------------------------------------------------------

def test_admin_required_endpoint(fixtureFunc):
    """root 用户能够访问 @admin_required 接口"""
    with app.test_client() as c:
        rv = c.get(
            "/cms/admin/permission",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200
