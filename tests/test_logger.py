"""
Logger 装饰器回归测试
~~~~~~~~~~~~~~~~~~~~~
验证 Logger 装饰器在 raise 和 return 两种路径下的行为：
- 成功类异常（Success/Created/Updated/Deleted）应当记录日志并重新抛出
- 失败类异常（Failed/NotFound/Forbidden 等）不应记录日志，直接抛出
- 正常 return 仍记录日志（向后兼容）
- 非 APIException 异常不记录日志，直接传播
"""

from flask import Blueprint
from app.lin import (
    Created,
    Deleted,
    Failed,
    Forbidden,
    Logger,
    NotFound,
    ParameterError,
    Success,
    Updated,
    db,
    login_required,
)
from app.lin.logger import Log

from . import app, fixtureFunc, get_token

# ── 测试蓝图与端点 ──────────────────────────────────────────────

logger_test_bp = Blueprint("logger_test", __name__)


@logger_test_bp.route("/logger_test/success", methods=["GET"])
@Logger(template="测试 Success 日志")
@login_required
def raise_success():
    raise Success("测试成功")


@logger_test_bp.route("/logger_test/created", methods=["GET"])
@Logger(template="测试 Created 日志")
@login_required
def raise_created():
    raise Created("测试创建")


@logger_test_bp.route("/logger_test/updated", methods=["GET"])
@Logger(template="测试 Updated 日志")
@login_required
def raise_updated():
    raise Updated("测试更新")


@logger_test_bp.route("/logger_test/deleted", methods=["GET"])
@Logger(template="测试 Deleted 日志")
@login_required
def raise_deleted():
    raise Deleted("测试删除")


@logger_test_bp.route("/logger_test/failed", methods=["GET"])
@Logger(template="测试 Failed 日志")
@login_required
def raise_failed():
    raise Failed("测试失败")


@logger_test_bp.route("/logger_test/not_found", methods=["GET"])
@Logger(template="测试 NotFound 日志")
@login_required
def raise_not_found():
    raise NotFound("测试未找到")


@logger_test_bp.route("/logger_test/forbidden", methods=["GET"])
@Logger(template="测试 Forbidden 日志")
@login_required
def raise_forbidden():
    raise Forbidden("测试禁止")


@logger_test_bp.route("/logger_test/normal_return", methods=["GET"])
@Logger(template="测试正常返回日志")
@login_required
def normal_return():
    return {"message": "正常返回"}


@logger_test_bp.route("/logger_test/value_error", methods=["GET"])
@Logger(template="测试 ValueError 日志")
@login_required
def raise_value_error():
    raise ValueError("非 API 异常")


@logger_test_bp.route("/logger_test/template_var", methods=["GET"])
@Logger(template="{user.username}执行了操作，响应码为{response.code}")
@login_required
def raise_with_template():
    raise Success("模板测试")


# 注册蓝图（仅在未注册时注册）
if "logger_test" not in app.blueprints:
    app.register_blueprint(logger_test_bp)


# ── 辅助函数 ────────────────────────────────────────────────────

def _clean_logs():
    """清除所有测试日志记录"""
    with app.app_context():
        logs = Log.query.filter_by(is_deleted=False).all()
        for log in logs:
            log.hard_delete()
        db.session.commit()


def _count_logs():
    """统计当前日志记录数"""
    with app.app_context():
        return Log.query.filter_by(is_deleted=False).count()


def _get_latest_log():
    """获取最新一条日志记录"""
    with app.app_context():
        return (
            Log.query.filter_by(is_deleted=False)
            .order_by(Log.create_time.desc())
            .first()
        )


# ── 测试用例 ────────────────────────────────────────────────────

def test_logger_logs_on_raise_success(fixtureFunc):
    """raise Success 时应写入日志，status_code=200"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/success",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["message"] == "测试成功"
        assert data["code"] == 0

    log = _get_latest_log()
    assert log is not None
    assert log.message == "测试 Success 日志"
    assert log.status_code == 200


def test_logger_logs_on_raise_created(fixtureFunc):
    """raise Created 时应写入日志，status_code=201"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/created",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 201

    log = _get_latest_log()
    assert log is not None
    assert log.message == "测试 Created 日志"
    assert log.status_code == 201


def test_logger_logs_on_raise_updated(fixtureFunc):
    """raise Updated 时应写入日志，status_code=200"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/updated",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200

    log = _get_latest_log()
    assert log is not None
    assert log.message == "测试 Updated 日志"
    assert log.status_code == 200


def test_logger_logs_on_raise_deleted(fixtureFunc):
    """raise Deleted 时应写入日志，status_code=200"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/deleted",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200

    log = _get_latest_log()
    assert log is not None
    assert log.message == "测试 Deleted 日志"
    assert log.status_code == 200


def test_logger_does_not_log_on_raise_failed(fixtureFunc):
    """raise Failed 时不应写入日志"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/failed",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 400

    assert _count_logs() == 0


def test_logger_does_not_log_on_raise_not_found(fixtureFunc):
    """raise NotFound 时不应写入日志"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/not_found",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 404

    assert _count_logs() == 0


def test_logger_does_not_log_on_raise_forbidden(fixtureFunc):
    """raise Forbidden 时不应写入日志"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/forbidden",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 401

    assert _count_logs() == 0


def test_logger_http_response_on_success(fixtureFunc):
    """raise Success 后 HTTP 响应应为 200 且包含正确的 JSON body"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/success",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert "message" in data
        assert "code" in data
        assert "request" in data
        assert data["code"] == 0


def test_logger_http_response_on_failure(fixtureFunc):
    """raise Failed 后 HTTP 响应应为 400 且包含正确的 JSON body"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/failed",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 400
        data = rv.get_json()
        assert data["message"] == "测试失败"
        assert data["code"] == 10200
        assert "request" in data


def test_logger_template_interpolation_with_exception(fixtureFunc):
    """模板 {user.username}...{response.code} 在异常路径下应正确插值"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/template_var",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200

    log = _get_latest_log()
    assert log is not None
    # 模板应包含用户名和响应码
    assert "执行了操作" in log.message
    assert "响应码为200" in log.message


def test_logger_logs_on_normal_return(fixtureFunc):
    """正常 return 仍应记录日志（向后兼容）"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/normal_return",
            headers={"Authorization": "Bearer " + get_token()},
        )
        assert rv.status_code == 200

    log = _get_latest_log()
    assert log is not None
    assert log.message == "测试正常返回日志"


def test_logger_non_api_exception_propagates(fixtureFunc):
    """非 APIException 异常（如 ValueError）应直接传播，不记录日志"""
    _clean_logs()
    with app.test_client() as c:
        rv = c.get(
            "/logger_test/value_error",
            headers={"Authorization": "Bearer " + get_token()},
        )
        # 非 APIException 会被全局异常处理器捕获，返回 500
        assert rv.status_code == 500

    assert _count_logs() == 0
