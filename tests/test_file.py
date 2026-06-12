"""上传契约回归测试。

覆盖本地 / COS / OSS 三条上传链路在以下方面已对齐：
- 扩展名大小写校验（统一大小写不敏感）；
- 重复文件按内容 md5 稳定复用已有记录；
- 返回统一的文件元数据契约 {id, key, url, file_name, file_key, size}，
  同时保留各后端语义（本地 path / COS 临时链接 / OSS 远程地址）。

测试不依赖 HTTP 登录，也不依赖云端网络：
- 本地链路直接驱动 LocalUploader；
- COS 通过替换模块级 client + 桩化 URL 生成；
- OSS 通过注入假的 uploader 回调；
- 重复文件用例使用 os.urandom 生成唯一内容，避免与历史数据冲突。
"""

import hashlib
import io
import os

import pytest
from werkzeug.datastructures import FileStorage, MultiDict

from app.lin import db
from app.lin.file import extension_allowed, file_meta, generate_md5, normalize_extension

from . import app

CONTRACT_KEYS = {"id", "key", "url", "file_name", "file_key", "size"}


@pytest.fixture(scope="module")
def ctx():
    """推入应用上下文并保证三条链路的数据表存在。"""
    with app.app_context():
        # 导入即把模型注册到 db.metadata，create_all 只会创建缺失的表
        from app.extension.file.file import File  # noqa: F401
        from app.plugin.cos.app.model import COS  # noqa: F401
        from app.plugin.oss.app.model import OSS  # noqa: F401

        db.create_all()
        yield


# --------------------------------------------------------------------------- #
# 1. 共享契约 helper（纯函数，不依赖 app 上下文）
# --------------------------------------------------------------------------- #
def test_extension_allowed_is_case_insensitive():
    assert extension_allowed("photo.jpg", {"jpg", "png"})
    assert extension_allowed("photo.JPG", {"jpg", "png"})
    assert extension_allowed("photo.PNG", ["png"])
    # 允许列表本身带大写也应被规整
    assert extension_allowed("photo.jpg", {"JPG"})


def test_extension_allowed_rejects_disallowed_or_missing_ext():
    assert not extension_allowed("photo.gif", {"jpg", "png"})
    assert not extension_allowed("noext", {"jpg"})
    assert not extension_allowed("photo.png", None)
    assert not extension_allowed("photo.png", [])


def test_normalize_extension_lowercases_with_dot():
    assert normalize_extension("X.PNG") == ".png"
    assert normalize_extension("IMG.JPEG") == ".jpeg"
    assert normalize_extension("a.tar.GZ") == ".gz"


def test_generate_md5_matches_hashlib_and_is_stable():
    assert generate_md5(b"hello") == hashlib.md5(b"hello").hexdigest()
    assert generate_md5(b"hello") == generate_md5(b"hello")
    assert generate_md5(b"hello") != generate_md5(b"world")


def test_file_meta_enforces_contract_keys():
    meta = file_meta(id=1, key="img", url="u", file_name="a.png", file_key="fk", size=3)
    assert set(meta) == CONTRACT_KEYS
    # 历史字段通过 extra 附加（如本地 path）
    meta2 = file_meta(id=1, key="img", url="u", file_name="a.png", file_key="fk", size=3, path="fk")
    assert set(meta2) == CONTRACT_KEYS | {"path"}
    assert meta2["path"] == "fk"


# --------------------------------------------------------------------------- #
# 2. 本地链路：端到端（统一契约 + md5 复用 + 扩展名小写归一）
# --------------------------------------------------------------------------- #
def _local_uploader(payload: bytes, filename: str, store_dir: str):
    from app.extension.file.local_uploader import LocalUploader

    files = MultiDict([("img", FileStorage(io.BytesIO(payload), filename=filename, name="img"))])
    config = {
        "STORE_DIR": store_dir,
        "INCLUDE": {"png"},
        "EXCLUDE": set(),
        "SINGLE_LIMIT": 2 * 1024 * 1024,
        "TOTAL_LIMIT": 20 * 1024 * 1024,
        "NUMS": 10,
    }
    return LocalUploader(files, config=config)


def test_local_upload_contract_and_dedup(ctx, tmp_path):
    payload = os.urandom(64)
    store = str(tmp_path)

    first = _local_uploader(payload, "a.PNG", store).upload()
    assert len(first) == 1
    item = first[0]
    # 统一契约字段齐全，且保留本地历史字段 path
    assert set(item) == CONTRACT_KEYS | {"path"}
    assert item["key"] == "img"
    assert item["file_name"] == "a.PNG"
    assert item["size"] == len(payload)
    # 存储扩展名归一为小写
    assert item["file_key"].endswith(".png")
    assert item["path"] == item["file_key"]

    # 相同内容再次上传 -> 复用同一条记录
    second = _local_uploader(payload, "a.PNG", store).upload()
    assert second[0]["id"] == item["id"]


# --------------------------------------------------------------------------- #
# 3. 模型层 md5 去重查询（COS / OSS 新 schema）
# --------------------------------------------------------------------------- #
def test_cos_model_dedup_by_md5(ctx):
    from app.plugin.cos.app.model import COS

    md5 = generate_md5(os.urandom(32))
    COS.create(file_name="a.png", file_key="k", file_md5=md5, file_size=1, status="UPLOADED", commit=True)
    assert COS.get(file_md5=md5) is not None
    assert COS.get(file_md5="not-exist-" + md5) is None


def test_oss_model_dedup_by_md5(ctx):
    from app.plugin.oss.app.model import OSS

    md5 = generate_md5(os.urandom(32))
    OSS.create(url="u", file_md5=md5, file_key="k", file_name="a.png", file_size=1, commit=True)
    assert OSS.get(file_md5=md5) is not None
    assert OSS.get(file_md5="not-exist-" + md5) is None


# --------------------------------------------------------------------------- #
# 4. COS 控制器：统一契约 + md5 复用（桩化 client / URL，无网络）
# --------------------------------------------------------------------------- #
def test_cos_controller_contract_and_dedup(ctx, monkeypatch):
    import app.plugin.cos.app.controller as cc
    from app.lin import lin_config

    lin_config.add_plugin_config(
        "cos",
        {
            "bucket_name": "bucket",
            "expire_time": 60,
            "need_return_url": False,
            "need_save_url": False,
            "upload_folder": "",
            "allowed_extensions": ["png"],
        },
    )

    class FakeClient:
        def put_object(self, **kwargs):
            return None

    monkeypatch.setattr(cc, "client", FakeClient())
    monkeypatch.setattr(cc.COS, "get_url", lambda *a, **k: "https://perm")
    monkeypatch.setattr(cc.COS, "get_presigned_url", lambda *a, **k: "https://signed")

    payload = os.urandom(50)
    r1 = cc.upload_image_and_create_cos("a.PNG", payload, key="image")
    assert set(r1) == CONTRACT_KEYS
    assert r1["key"] == "image"
    assert r1["file_name"] == "a.PNG"
    assert r1["size"] == len(payload)
    assert r1["file_key"].endswith(".png")  # 扩展名小写归一
    assert r1["url"] == "https://signed"  # 默认返回临时链接，语义保留

    # 相同内容、不同文件名 -> md5 复用同一记录
    r2 = cc.upload_image_and_create_cos("DIFFERENT.PNG", payload, key="image")
    assert r2["id"] == r1["id"]


# --------------------------------------------------------------------------- #
# 5. OSS 控制器：统一契约 + 先去重后上传（注入假 uploader，无 oss2 / 网络）
# --------------------------------------------------------------------------- #
def test_oss_controller_contract_and_dedup_before_upload(ctx):
    import app.plugin.oss.app.controller as oc

    calls = []

    def fake_uploader(object_key, data):
        calls.append(object_key)
        return "https://ali/" + object_key

    payload = os.urandom(50)
    e1 = oc._store_and_envelope("a.PNG", payload, key="image", uploader=fake_uploader)
    assert set(e1) == CONTRACT_KEYS
    assert e1["key"] == "image"
    assert e1["file_name"] == "a.PNG"
    assert e1["size"] == len(payload)
    assert e1["file_key"].endswith(".png")  # 扩展名小写归一
    assert e1["url"].startswith("https://ali/")  # 远程地址语义保留

    # 相同内容、不同文件名 -> 复用记录且不再触发上传
    e2 = oc._store_and_envelope("b.PNG", payload, key="image", uploader=fake_uploader)
    assert e2["id"] == e1["id"]
    assert len(calls) == 1
