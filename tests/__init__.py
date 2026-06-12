import json
import os

import pytest

from .config import password, username

# 说明：应用的构造从「模块导入时」改为「首次访问 app 时」（PEP 562 模块级 __getattr__）。
# 这样仅导入 tests 包不会触发完整 Flask 应用栈的加载，便于不依赖应用栈的测试（如插件
# 初始化的回归测试）被收集运行；而 `from . import app` 的既有用法行为保持不变。

_app = None


def _get_app():
    global _app
    if _app is None:
        from app import create_app
        from app.api.cms.model.group import Group
        from app.api.cms.model.group_permission import GroupPermission
        from app.api.cms.model.permission import Permission
        from app.api.cms.model.user import User
        from app.api.cms.model.user_group import UserGroup
        from app.api.cms.model.user_identity import UserIdentity

        _app = create_app(
            group_model=Group,
            user_model=User,
            group_permission_model=GroupPermission,
            permission_model=Permission,
            identity_model=UserIdentity,
            user_group_model=UserGroup,
        )
    return _app


def __getattr__(name):
    # 让 `from . import app` 在首次访问时才真正构造应用
    if name == "app":
        return _get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@pytest.fixture()
def fixtureFunc():
    app = _get_app()
    with app.test_client() as c:
        rv = c.post(
            "/cms/user/login",
            headers={"Content-Type": "application/json"},
            json={"username": username, "password": password},
        )
        json_data = rv.get_json()
        assert json_data.get("access_token") != None
        assert rv.status_code == 200
        write_token(json_data)


def get_file_path():
    pytest_cache_dir_path = os.getcwd() + os.path.sep + ".pytest_cache"
    if not os.path.exists(pytest_cache_dir_path):
        os.makedirs(pytest_cache_dir_path)
    json_file_path = pytest_cache_dir_path + os.path.sep + "test.json"
    return json_file_path


def write_token(data):
    obj = json.dumps(data)
    with open(get_file_path(), "w") as f:
        f.write(obj)


def get_token(key="access_token"):
    with open(get_file_path(), "r") as f:
        obj = json.loads(f.read())
        return obj[key]
