import os
import uuid

from flask import jsonify, request

from app.lin import (
    Failed,
    ParameterError,
    Redprint,
    Success,
    db,
    extension_allowed,
    file_meta,
    generate_md5,
    lin_config,
    normalize_extension,
)

from .model import OSS

api = Redprint("oss")


@api.route("/upload_to_local", methods=["POST"])
def upload():
    image = request.files.get("image", None)
    if not image:
        raise ParameterError("没有找到图片")
    if image and allowed_file(image.filename):
        path = os.path.join(lin_config.get_config("oss.upload_folder"), image.filename)
        image.save(path)
    else:
        raise ParameterError("图片类型不允许或图片key不合法")
    raise Success()


@api.route("/upload_to_ali", methods=["POST"])
def upload_to_ali():
    image = request.files.get("image", None)
    if not image:
        raise ParameterError("没有找到图片")
    if image and allowed_file(image.filename):
        result = _store_and_envelope(image.filename, image.read(), key="image", uploader=_oss_uploader)
        if result:
            return jsonify(result)
    return Failed("上传图片失败，请检查图片路径")


@api.route("/upload_multiple", methods=["POST"])
def upload_multiple_to_ali():
    imgs = []
    for item in request.files:
        img = request.files.get(item, None)
        if not img:
            raise ParameterError("没接收到图片，请检查图片路径")
        if img and allowed_file(img.filename):
            result = _store_and_envelope(img.filename, img.read(), key=item, uploader=_oss_uploader)
            if result:
                imgs.append(result)
    return jsonify(imgs)


def allowed_file(filename):
    return extension_allowed(filename, lin_config.get_config("oss.allowed_extensions", []))


def _store_and_envelope(name, data, key, uploader):
    """按内容 md5 复用已有记录，未命中才真正上传，并返回统一的文件元数据契约。

    上传动作通过注入的 ``uploader(object_key, data) -> url`` 完成，便于在不依赖
    oss2 / 网络的情况下做回归测试。返回 ``None`` 表示上传失败。
    """
    file_md5 = generate_md5(data)
    size = len(data)
    # 按文件内容 md5 复用已有记录，与本地 / COS 链路保持一致；命中则不再重复上传
    exist = OSS.get(file_md5=file_md5)
    if exist:
        return file_meta(
            id=exist.id,
            key=key,
            url=exist.url,
            file_name=name,
            file_key=exist.file_key,
            size=size,
        )

    object_key = str(uuid.uuid1()) + normalize_extension(name)
    url = uploader(object_key, data)
    if not url:
        return None
    with db.auto_commit():
        one = OSS.create(
            url=url,
            file_md5=file_md5,
            file_key=object_key,
            file_name=name,
            file_size=size,
            commit=True,
        )
        oss_id = one.id
    return file_meta(
        id=oss_id,
        key=key,
        url=url,
        file_name=name,
        file_key=object_key,
        size=size,
    )


def _oss_uploader(object_key: str, data: bytes):
    import oss2

    access_key_id = lin_config.get_config("oss.access_key_id")
    access_key_secret = lin_config.get_config("oss.access_key_secret")
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(
        auth,
        lin_config.get_config("oss.endpoint"),
        lin_config.get_config("oss.bucket_name"),
    )
    res = bucket.put_object(object_key, data)
    if res.resp.status == 200:
        return res.resp.response.url
    return None
