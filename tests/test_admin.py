"""
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

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


def test_delete_user_cleans_up_user_group_and_identity(fixtureFunc):
    """删除用户后，user_group 和 user_identity 关联记录应一并清除，不留孤儿数据"""
    from app.lin.db import db as _db
    from app.lin import manager

    token = get_token()
    test_username = "deltest"
    test_password = "123456"

    with app.test_client() as c:
        # 1. 注册一个测试用户
        rv = c.post(
            "/cms/user/register",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            json={"username": test_username, "password": test_password, "confirm_password": test_password, "group_ids": []},
        )
        assert rv.status_code == 200

        # 2. 查出该用户的 id
        with app.app_context():
            user = manager.user_model.get(username=test_username)
            assert user is not None
            uid = user.id

            # 确认 user_group 和 user_identity 记录存在
            ug_count = manager.user_group_model.query.filter_by(user_id=uid).count()
            assert ug_count > 0, "删除前 user_group 记录应存在"
            ui_count = manager.identity_model.query.filter_by(user_id=uid).count()
            assert ui_count > 0, "删除前 user_identity 记录应存在"

        # 3. 删除该用户
        rv = c.delete(
            f"/cms/admin/user/{uid}",
            headers={"Authorization": "Bearer " + token},
        )
        assert rv.status_code == 200
        with app.app_context():
            remaining_ug = manager.user_group_model.query.filter_by(user_id=uid).count()
            assert remaining_ug == 0, "删除后 user_group 不应有孤儿记录"

            remaining_ui = manager.identity_model.query.filter_by(user_id=uid).count()
            assert remaining_ui == 0, "删除后 user_identity 不应有孤儿记录"

            # 用户本身也应被硬删除
            deleted_user = manager.user_model.query.filter_by(id=uid).first()
            assert deleted_user is None, "用户记录应已被硬删除"


def test_delete_root_user_is_forbidden(fixtureFunc):
    """Root 用户不可删除"""
    from app.lin import manager

    token = get_token()

    with app.app_context():
        root_user = manager.user_model.get(username="root")
        assert root_user is not None
        root_uid = root_user.id

    with app.test_client() as c:
        rv = c.delete(
            f"/cms/admin/user/{root_uid}",
            headers={"Authorization": "Bearer " + token},
        )
        # 应返回 Forbidden (HTTP 403 in lin-cms is status 401 for Forbidden)
        assert rv.status_code == 401


def test_delete_nonexistent_user_returns_404(fixtureFunc):
    """删除不存在的用户应返回 404"""
    token = get_token()

    with app.test_client() as c:
        rv = c.delete(
            "/cms/admin/user/999999",
            headers={"Authorization": "Bearer " + token},
        )
        assert rv.status_code == 404
