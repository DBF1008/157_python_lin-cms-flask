import os

import oss2
from flask import jsonify, request

from app.lin import Failed, ParameterError, Redprint, Success, db, lin_config

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
    if not (image and allowed_file(image.filename)):
        return Failed("上传图片失败，请检查图片类型")

    data = image.read()
    file_md5 = OSS.generate_md5(data)

    # Dedup by MD5 — same content reuses existing record
    exist = OSS.select_by_md5(file_md5)
    if exist:
        return jsonify(_build_oss_response(image.name, exist))

    url, file_key = upload_image_bytes(image.filename, data)
    if not url:
        return Failed("上传图片失败，请检查图片路径")

    ext = "." + image.filename.lower().rsplit(".", 1)[-1]
    with db.auto_commit():
        one = OSS.create(
            file_name=image.filename,
            file_key=file_key,
            file_md5=file_md5,
            file_size=len(data),
            extension=ext,
            url=url,
            commit=True,
        )
    return jsonify(_build_oss_response(image.name, one))


@api.route("/upload_multiple", methods=["POST"])
def upload_multiple_to_ali():
    imgs = []
    for item in request.files:
        img = request.files.get(item, None)
        if not img:
            raise ParameterError("没接收到图片，请检查图片路径")
        if not (img and allowed_file(img.filename)):
            continue

        data = img.read()
        file_md5 = OSS.generate_md5(data)

        # Dedup by MD5 — same content reuses existing record
        exist = OSS.select_by_md5(file_md5)
        if exist:
            imgs.append(_build_oss_response(item, exist))
            continue

        url, file_key = upload_image_bytes(img.filename, data)
        if not url:
            continue

        ext = "." + img.filename.lower().rsplit(".", 1)[-1]
        with db.auto_commit():
            one = OSS.create(
                file_name=img.filename,
                file_key=file_key,
                file_md5=file_md5,
                file_size=len(data),
                extension=ext,
                url=url,
                commit=True,
            )
        imgs.append(_build_oss_response(item, one))
    return jsonify(imgs)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in lin_config.get_config(
        "oss.allowed_extensions", []
    )


def upload_image_bytes(name: str, data: bytes):
    """Upload bytes to Alibaba OSS. Returns (url, file_key) or (None, None) on failure."""
    access_key_id = lin_config.get_config("oss.access_key_id")
    access_key_secret = lin_config.get_config("oss.access_key_secret")
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(
        auth,
        lin_config.get_config("oss.endpoint"),
        lin_config.get_config("oss.bucket_name"),
    )
    file_key = OSS.generate_key(name)
    res = bucket.put_object(file_key, data)
    if res.resp.status == 200:
        return res.resp.response.url, file_key
    return None, None


def _build_oss_response(key, record):
    """Build a unified response dict with common fields."""
    stored_name = record.file_key.rsplit("/", 1)[-1] if record.file_key else ""
    return {
        "key": key,
        "id": record.id,
        "name": stored_name,
        "path": record.file_key,
        "url": record.url,
        "size": record.file_size,
        "extension": record.extension,
        "md5": record.file_md5,
        "type": record.type,
    }
