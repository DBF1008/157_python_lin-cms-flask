"""
Notify 装饰器回归测试
~~~~~~~~~~~~~~~~~~~~~
验证 Notify 装饰器在 raise 和 return 两种路径下的行为：
- 成功类异常（Success/Created/Updated/Deleted）应当推送 SSE 消息并重新抛出
- 失败类异常（Failed/NotFound 等）不应推送消息，直接抛出
- 正常 return 仍推送消息（向后兼容）
- 非 APIException 异常不推送消息，直接传播
"""

from flask import Blueprint
from app.lin import (
    Failed,
    NotFound,
    Success,
    login_required,
)
from app.extension.notify.notify import Notify
from app.extension.notify.sse import sser

from . import app, fixtureFunc, get_token

# ── 测试蓝图与端点 ──────────────────────────────────────────────

notify_test_bp = Blueprint("notify_test", __name__)


@notify_test_bp.route("/notify_test/success", methods=["GET"])
@Notify(template="测试 Notify Success", event="test_success")
@login_required
def raise_success():
    raise Success("通知测试成功")


@notify_test_bp.route("/notify_test/failed", methods=["GET"])
@Notify(template="测试 Notify Failed", event="test_failed")
@login_required
def raise_failed():
    raise Failed("通知测试失败")


@notify_test_bp.route("/notify_test/normal_return", methods=["GET"])
@Notify(template="测试 Notify 正常返回", event="test_normal")
@login_required
def normal_return():
    return {"message": "正常返回"}


@notify_test_bp.route("/notify_test/value_error", methods=["GET"])
@Notify(template="测试 Notify ValueError", event="test_error")
@login_required
def raise_value_error():
    raise ValueError("非 API 异常")


# 注册蓝图（仅在未注册时注册）
if "notify_test" not in app.blueprints:
    app.register_blueprint(notify_test_bp)


# ── 辅助函数 ────────────────────────────────────────────────────

def _clear_sse_messages():
    """清空 SSE 消息队列"""
    sser.messages.clear()


def _get_sse_message_count():
    """获取 SSE 消息队列中的消息数量"""
    return len(sser.messages)


def _get_latest_sse_message():
    """获取最新的 SSE 消息"""
    if sser.messages:
        return sser.messages[-1]
    return None


# ── 测试用例 ────────────────────────────────────────────────────

def test_notify_pushes_on_raise_success(fixtureFunc):
    """raise Success 时应推送 SSE 消息"""
    _clear_sse_messages()
    with app.test_client() as c:
        rv = c.get(
            "/notify_test/success",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["message"] == "通知测试成功"

    assert _get_sse_message_count() > 0
    msg = _get_latest_sse_message()
    assert msg is not None
    assert "test_success" in msg  # event name
    assert "测试 Notify Success" in msg  # template message


def test_notify_does_not_push_on_raise_failed(fixtureFunc):
    """raise Failed 时不应推送 SSE 消息"""
    _clear_sse_messages()
    with app.test_client() as c:
        rv = c.get(
            "/notify_test/failed",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 400

    assert _get_sse_message_count() == 0


def test_notify_pushes_on_normal_return(fixtureFunc):
    """正常 return 仍应推送 SSE 消息（向后兼容）"""
    _clear_sse_messages()
    with app.test_client() as c:
        rv = c.get(
            "/notify_test/normal_return",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200

    assert _get_sse_message_count() > 0
    msg = _get_latest_sse_message()
    assert msg is not None
    assert "test_normal" in msg
    assert "测试 Notify 正常返回" in msg


def test_notify_exception_propagates(fixtureFunc):
    """非 APIException 异常（如 ValueError）应直接传播，不推送消息"""
    _clear_sse_messages()
    with app.test_client() as c:
        rv = c.get(
            "/notify_test/value_error",
            headers={"Authorization": "Bearer " + get_token()},
        )
        # 非 APIException 会被全局异常处理器捕获，返回 500
        assert rv.status_code == 500

    assert _get_sse_message_count() == 0
