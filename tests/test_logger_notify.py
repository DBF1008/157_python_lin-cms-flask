"""
回归验证: 行为日志(Logger)与消息推送(Notify)装饰器。

覆盖目标:
- 通过 ``raise Success`` / ``return Response`` 两种方式结束的成功请求都会被记录 / 推送;
- 成功结果的状态码与模板变量(``{response.status_code}``)在 raise 路径上不再丢失;
- 失败请求(``raise Failed`` 或 ``return Failed(...)``)不会被误记 / 误推。

:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import uuid

import pytest
from flask import Response

from app.extension.notify.notify import Notify
from app.lin.exception import Created, Failed, Success
from app.lin.logger import Log, Logger

from . import app, fixtureFunc, get_token  # noqa: F401  (app/fixtureFunc/get_token 供下方测试与 fixture 使用)


# ---------------------------------------------------------------------------
# 集成测试: 证明“旧链路”被修复 —— @Logger + raise Success 现在会写入行为日志
# ---------------------------------------------------------------------------
def test_register_via_raise_success_writes_log(fixtureFunc):
    """
    POST /cms/user/register 的实现以 ``raise Success`` 结束, 且被 @Logger 装饰。

    修复前: 异常绕过了 write_log(), 日志丢失;
    修复后: 应返回 200, 并新增一条对应的行为日志(状态码 200 不丢)。
    """
    message = "管理员新建了一个用户"
    path = "/cms/user/register"
    username = "reg_" + uuid.uuid4().hex[:6]

    with app.app_context():
        before = Log.query.filter_by(message=message, path=path).count()

    with app.test_client() as c:
        rv = c.post(
            path,
            headers={"Authorization": "Bearer " + get_token()},
            json={
                "username": username,
                "password": "123456",
                "confirm_password": "123456",
                "group_ids": [],
            },
        )

    # raise Success -> 全局异常处理生成响应, 状态码保留为 200
    assert rv.status_code == 200

    with app.app_context():
        logs = Log.query.filter_by(message=message, path=path).order_by(Log.id.desc()).all()
        # 修复前该值恒为 before(日志丢失); 修复后应当 +1
        assert len(logs) == before + 1
        latest = logs[0]
        assert latest.status_code == 200
        assert latest.method == "POST"

        _cleanup_user(username)


def _cleanup_user(username):
    """删除集成测试创建的用户, 保证用例可重复运行(尽力而为, 不影响断言结果)。"""
    try:
        from app.api.cms.model.user import User
        from app.api.cms.model.user_group import UserGroup
        from app.lin import db

        created = User.query.filter_by(username=username).first()
        if created is not None:
            UserGroup.query.filter_by(user_id=created.id).delete(synchronize_session=False)
            created.hard_delete()
            db.session.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 单元测试: 直接驱动两个装饰器, 覆盖 成功/失败 × raise/return 的完整契约
# ---------------------------------------------------------------------------
class _FakeUser:
    id = 1
    username = "tester"
    is_admin = False


@pytest.fixture()
def fake_login(monkeypatch):
    """让两个装饰器内部的 get_current_user() 返回固定用户, 无需真实 JWT。"""
    user = _FakeUser()
    monkeypatch.setattr("app.lin.logger.get_current_user", lambda: user)
    monkeypatch.setattr("app.extension.notify.notify.get_current_user", lambda: user)
    return user


def _make_logger(monkeypatch, template):
    """构造 Logger 并用记录器替换 write_log(避免触达数据库), 返回 (实例, 记录列表)。"""
    records = []

    def fake_write_log(self):
        records.append({"message": self.message, "status_code": self.response.status_code})

    monkeypatch.setattr(Logger, "write_log", fake_write_log)
    return Logger(template=template), records


def _make_notify(monkeypatch, template, event="test_event"):
    """构造 Notify 并用记录器替换 push_message, 返回 (实例, 推送列表)。"""
    pushes = []

    def fake_push(self):
        pushes.append({"message": self.message, "status_code": self.response.status_code})

    monkeypatch.setattr(Notify, "push_message", fake_push)
    return Notify(template=template, event=event), pushes


# ---- Logger ----------------------------------------------------------------
def test_logger_records_on_raise_success(monkeypatch, fake_login):
    logger, records = _make_logger(monkeypatch, "{user.username}操作成功,状态码{response.status_code}")

    @logger
    def view():
        raise Success("ok")

    with pytest.raises(Success):  # 成功异常被重新抛出, 交由全局处理生成响应
        view()

    assert len(records) == 1
    # 关键: {response.status_code} 在 raise 路径上仍解析为 200, 模板变量不丢
    assert records[0]["status_code"] == 200
    assert records[0]["message"] == "tester操作成功,状态码200"


def test_logger_skips_on_raise_failed(monkeypatch, fake_login):
    logger, records = _make_logger(monkeypatch, "{user.username}操作")

    @logger
    def view():
        raise Failed("nope")

    with pytest.raises(Failed):
        view()

    assert records == []  # 失败请求不记录


def test_logger_skips_on_return_failed(monkeypatch, fake_login):
    """change_password 风格: 失败时 ``return Failed(...)`` —— 修复前会被误记, 现在不应记录。"""
    logger, records = _make_logger(monkeypatch, "{user.username}操作")

    @logger
    def view():
        return Failed("nope")

    result = view()
    assert isinstance(result, Failed)  # 原样返回, 交由 Flask 处理
    assert records == []


def test_logger_records_on_return_response(monkeypatch, fake_login):
    logger, records = _make_logger(monkeypatch, "状态码{response.status_code}")

    @logger
    def view():
        return Response("body", status=200)

    result = view()
    assert isinstance(result, Response)
    assert len(records) == 1
    assert records[0]["status_code"] == 200


# ---- Notify ----------------------------------------------------------------
def test_notify_pushes_on_raise_success(monkeypatch, fake_login):
    notify, pushes = _make_notify(monkeypatch, "{user.username}操作成功,状态码{response.status_code}")

    @notify
    def view():
        raise Created("created")

    with pytest.raises(Created):
        view()

    assert len(pushes) == 1
    assert pushes[0]["status_code"] == 201  # Created -> 201, 状态码不丢
    assert pushes[0]["message"] == "tester操作成功,状态码201"


def test_notify_skips_on_failure(monkeypatch, fake_login):
    notify, pushes = _make_notify(monkeypatch, "{user.username}操作")

    @notify
    def raise_view():
        raise Failed("nope")

    with pytest.raises(Failed):
        raise_view()
    assert pushes == []

    @notify
    def return_view():
        return Failed("nope")

    assert isinstance(return_view(), Failed)
    assert pushes == []
