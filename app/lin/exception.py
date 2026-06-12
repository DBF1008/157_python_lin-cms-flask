"""
exceptions of Lin
~~~~~~~~~
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

from flask import json, request
from werkzeug.exceptions import HTTPException

from .config import global_config


class APIException(HTTPException):
    code = 500
    message = "抱歉，服务器未知错误"
    message_code = 9999
    headers = {"Content-Type": "application/json"}
    _config = True

    def __init__(self, *args):
        # 1. 没有参数
        if len(args) == 0:
            self.message = (
                global_config.get("MESSAGE", dict()).get(self.message_code, self.message)
                if self._config
                else self.message
            )
        # 2.1 一个参数，为数字
        elif len(args) == 1:
            if isinstance(args[0], int):
                self.message_code = args[0]
                self.message = (
                    global_config.get("MESSAGE", dict()).get(self.message_code, self.message)
                    if self._config
                    else self.message
                )
            # 2.2. 一个参数，为字符串 or 字典
            elif isinstance(args[0], (str, dict)):
                self.message = args[0]
        # 3. 两个参数， 一个整数，一个字符串 or 字典
        elif len(args) == 2:
            if isinstance(args[0], int) and isinstance(args[1], (str, dict)):
                self.message_code = args[0]
                self.message = args[1]
            elif isinstance(args[1], int) and isinstance(args[0], (str, dict)):
                self.message_code = args[1]
                self.message = args[0]
        # 最终都要调用父类方法
        super().__init__(self.message, None)

    def set_code(self, code: int):
        self.code = code
        return self

    def set_message_code(self, message_code: int):
        self.message_code = message_code
        return self

    def add_headers(self, headers: dict):
        headers_merged = headers.copy()
        headers_merged.update(self.headers)
        self.headers = headers_merged
        return self

    def get_body(self, environ=None, scope=None):
        body = dict(
            message=self.message,
            code=self.message_code,
            request=request.method + "  " + self.get_url_no_param(),
        )
        text = json.dumps(body)
        return text

    @staticmethod
    def get_url_no_param():
        full_path = str(request.full_path)
        main_path = full_path.split("?")
        return main_path[0]

    def get_headers(self, environ=None, scope=None):
        return [(k, v) for k, v in self.headers.items()]


class Success(APIException):
    code = 200
    message = "OK"
    message_code = 0


class Created(APIException):
    code = 201
    message = "Created"
    message_code = 1


class Updated(APIException):
    code = 200
    message = "Updated"
    message_code = 2


class Deleted(APIException):
    code = 200
    message = "Deleted"
    message_code = 3


class Failed(APIException):
    code = 400
    message = "Failed"
    message_code = 10200


class UnAuthorization(APIException):
    code = 401
    message = "Authorization Failed"
    message_code = 10000


class UnAuthentication(APIException):
    code = 401
    message = "Authentication Failed"
    message_code = 10010


class NotFound(APIException):
    code = 404
    message = "Not Found"
    message_code = 10021


class ParameterError(APIException):
    code = 400
    message = "Parameters Error"
    message_code = 10030


class TokenInvalid(APIException):
    code = 401
    message = "Token Invalid"
    message_code = 10040


class TokenExpired(APIException):
    code = 422
    message = "Token Expired"
    message_code = 10052


class InternalServerError(APIException):
    code = 500
    message = "Internal Server Error"
    message_code = 9999


class Duplicated(APIException):
    code = 400
    message = "Duplicated"
    message_code = 10060


class Forbidden(APIException):
    code = 401
    message = "Forbidden"
    message_code = 10070


class FileTooLarge(APIException):
    code = 413
    message = "File Too Large"
    message_code = 10110


class FileTooMany(APIException):
    code = 413
    message = "File Too Many"
    message_code = 10120


class FileExtensionError(APIException):
    code = 401
    message = "FIle Extension Not Allowed"
    message_code = 10130


class MethodNotAllowed(APIException):
    code = 401
    message = "Method Not Allowed"
    message_code = 10080


class RequestLimit(APIException):
    code = 401
    message = "Too Many Requests"
    message_code = 10140


# 请求结果归一化工具
# ~~~~~~~~~~~~~~~~~~
# 行为日志(Logger)与消息推送(Notify)装饰器既要处理视图正常 return 的 Response,
# 也要处理视图通过 ``raise Success`` / ``raise Failed`` 结束请求的情况。
# 以下工具把两种结果“收口”为统一的成功判定与属性访问入口。

# 视为“成功”的 HTTP 状态码区间 [200, 300)
SUCCESS_CODE_RANGE = range(200, 300)


def _raw_status_code(result):
    """返回 result 自带的 HTTP 状态码, 若其本身不携带状态码则返回 None。

    - ``APIException``(含 Success/Failed 等)使用其 ``code``;
    - Flask ``Response`` 使用 ``status_code``;
    - 其余原始返回值(dict / schema / model / None)两者皆无, 返回 None。
    """
    if isinstance(result, APIException):
        return result.code
    status_code = getattr(result, "status_code", None)
    if status_code is None:
        status_code = getattr(result, "code", None)
    return status_code


def get_response_status_code(result) -> int:
    """取得请求结果的 HTTP 状态码, 无法判定时回退为 0(与历史行为一致)。"""
    status_code = _raw_status_code(result)
    return status_code if status_code is not None else 0


def is_success_response(result) -> bool:
    """判断请求结果是否成功: 仅成功结果才记录行为日志 / 推送消息。

    失败结果(如 ``Failed``, 无论是 raise 还是 return)一律跳过; 既无 ``status_code``
    也无 ``code`` 的原始返回值(视图直接返回数据)视为成功, 以保持“正常 return 一律
    记录”的历史行为。
    """
    status_code = _raw_status_code(result)
    if status_code is None:
        return True
    return status_code in SUCCESS_CODE_RANGE


class ResponseValue:
    """请求结果的统一视图, 供日志 / 推送的消息模板取值。

    无论视图是 ``return`` 一个 Response 还是 ``raise`` 一个 APIException, 都对外暴露
    稳定的 ``status_code``, 使 ``{response.status_code}`` 等模板变量不再丢失; 其余属性
    (如 ``message``)代理到被包装对象, 缺失时返回 "" , 与模板解析中
    ``getattr(..., prop, "")`` 的容错行为保持一致。
    """

    def __init__(self, result):
        self._result = result
        self.status_code = get_response_status_code(result)

    def __getattr__(self, name):
        return getattr(self._result, name, "")
