"""
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

from . import app, fixtureFunc, get_token

TEST_USERNAME = "del_test_user"


def _purge_test_user(username=TEST_USERNAME):
    """删除可能残留的测试用户及其关联/认证记录，保证用例可重复执行。"""
    with app.app_context():
        from app.api.cms.model.user import User
        from app.api.cms.model.user_group import UserGroup
        from app.api.cms.model.user_identity import UserIdentity
        from app.lin import db

        user = User.query.filter_by(username=username).first()
        if user is not None:
            UserGroup.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            UserIdentity.query.filter_by(user_id=user.id).delete(synchronize_session=False)
            user.hard_delete()
            db.session.commit()


def _create_regular_user(username=TEST_USERNAME, password="123456"):
    """在 Guest 分组下创建一个普通用户，并生成身份认证记录，返回用户 id。"""
    _purge_test_user(username)
    with app.app_context():
        from app.api.cms.model.group import Group
        from app.api.cms.model.user import User
        from app.api.cms.model.user_group import UserGroup
        from app.lin import GroupLevelEnum, db

        guest_group = Group.get(level=GroupLevelEnum.GUEST.value)
        assert guest_group is not None, "Guest 分组不存在，请先执行 flask db init"

        user = User()
        user.username = username
        db.session.add(user)
        db.session.flush()
        # 触发 password setter，写入 lin_user_identity 身份认证记录
        user.password = password

        user_group = UserGroup()
        user_group.user_id = user.id
        user_group.group_id = guest_group.id
        db.session.add(user_group)
        db.session.commit()
        return user.id


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


def test_delete_user_removes_group_and_identity(fixtureFunc):
    """删除普通用户应同时清理用户分组关联与身份认证记录，不留孤儿数据。"""
    uid = _create_regular_user()

    # 前置断言：删除前分组关联与身份认证记录均存在
    with app.app_context():
        from app.api.cms.model.user_group import UserGroup
        from app.api.cms.model.user_identity import UserIdentity

        assert UserGroup.query.filter_by(user_id=uid).count() >= 1
        assert UserIdentity.query.filter_by(user_id=uid).count() >= 1

    with app.test_client() as c:
        rv = c.delete(
            "/cms/admin/user/{}".format(uid),
            headers={"Authorization": "Bearer " + get_token()},
        )
    # 保留原有返回语义：删除成功返回 200 / Success
    assert rv.status_code == 200
    assert rv.get_json().get("code") == 0

    # 删除后：用户、分组关联、身份认证记录均被清理，无孤儿数据残留
    with app.app_context():
        from app.api.cms.model.user import User
        from app.api.cms.model.user_group import UserGroup
        from app.api.cms.model.user_identity import UserIdentity

        assert User.query.filter_by(id=uid).first() is None
        assert UserGroup.query.filter_by(user_id=uid).count() == 0
        assert UserIdentity.query.filter_by(user_id=uid).count() == 0


def test_delete_root_user_forbidden(fixtureFunc):
    """Root 用户不可删除，且其分组关联与身份认证记录保持完整。"""
    with app.app_context():
        from app.api.cms.model.user import User

        root = User.query.filter_by(username="root").first()
        assert root is not None
        root_id = root.id

    with app.test_client() as c:
        rv = c.delete(
            "/cms/admin/user/{}".format(root_id),
            headers={"Authorization": "Bearer " + get_token()},
        )
    # Forbidden 在本项目中映射为 401，message_code 为 10070
    assert rv.status_code == 401
    assert rv.get_json().get("code") == 10070

    # Root 用户及其关联/认证记录均未被删除
    with app.app_context():
        from app.api.cms.model.user import User
        from app.api.cms.model.user_group import UserGroup
        from app.api.cms.model.user_identity import UserIdentity

        assert User.query.filter_by(username="root").first() is not None
        assert UserGroup.query.filter_by(user_id=root_id).count() >= 1
        assert UserIdentity.query.filter_by(user_id=root_id).count() >= 1
