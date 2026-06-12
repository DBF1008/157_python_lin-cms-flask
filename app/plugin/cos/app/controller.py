from flask import request
from werkzeug.local import LocalProxy

from app.api import AuthorizationBearerSecurity, api
from app.lin import DocResponse, Failed, ParameterError, Redprint, db, extension_allowed, file_meta, lin_config, login_required

from .exception import ImageNotFound
from .model import COS
from .schema import CosOutSchema, CosOutSchemaList

client = LocalProxy(lambda: get_cos_client())

cos_api = Redprint("cos")


@cos_api.route("/<int:_id>")
@login_required
@api.validate(
    resp=DocResponse(ImageNotFound, r=CosOutSchema),
    tags=["cos"],
    security=[AuthorizationBearerSecurity],
)
def get_cos_image(_id):
    """
    获取指定 id 的 cos
    """
    cos = COS.get(id=_id)
    if cos:
        bucket = lin_config.get_config("cos.bucket_name")
        if lin_config.get_config("cos.need_return_url"):
            # 返回永久链接
            url = cos.url if cos.url else COS.get_url(client, bucket, cos.file_key)
        else:
            # 返回临时链接
            url = COS.get_presigned_url(client, bucket, cos.file_key)
        return file_meta(
            id=cos.id,
            key=None,
            url=url,
            file_name=cos.file_name,
            file_key=cos.file_key,
            size=cos.file_size,
        )
    raise ImageNotFound


@cos_api.route("/upload_one", methods=["POST"])
@login_required
@api.validate(
    resp=DocResponse(r=CosOutSchema),
    tags=["cos"],
    security=[AuthorizationBearerSecurity],
)
def upload_one():
    image = request.files.get("image", None)
    if not image:
        raise ParameterError("没有找到图片")
    if image and allowed_file(image.filename):
        return upload_image_and_create_cos(image.filename, image.read(), key="image")
    return Failed("上传图片失败，请检查图片路径")


@cos_api.route("/upload_multiple", methods=["POST"])
@login_required
@api.validate(
    resp=DocResponse(r=CosOutSchemaList),
    tags=["cos"],
    security=[AuthorizationBearerSecurity],
)
def upload_multiple():
    images = []
    for item in request.files:
        image = request.files.get(item, None)
        if not image:
            raise ParameterError("没接收到图片，请检查图片路径")
        if image and allowed_file(image.filename):
            images.append(upload_image_and_create_cos(image.filename, image.read(), key=item))
    return images


def upload_image_and_create_cos(name: str, data: bytes, key=None) -> dict:
    bucket = lin_config.get_config("cos.bucket_name")
    file_md5 = COS.generate_md5(data)
    size = len(data)

    def resolve_url(file_key, stored_url=None):
        if lin_config.get_config("cos.need_return_url"):
            # 返回永久链接
            return stored_url if stored_url else COS.get_url(client, bucket, file_key)
        # 返回临时链接
        return COS.get_presigned_url(client, bucket, file_key)

    # 按文件内容 md5 复用已有记录，与本地 / OSS 链路保持一致
    exist = COS.get(file_md5=file_md5)
    if exist:
        return file_meta(
            id=exist.id,
            key=key,
            url=resolve_url(exist.file_key, exist.url),
            file_name=name,
            file_key=exist.file_key,
            size=exist.file_size if exist.file_size is not None else size,
        )

    file_key = COS.generate_key(name)
    client.put_object(Bucket=bucket, Body=data, Key=file_key, StorageClass="STANDARD")
    permanent_url = COS.get_url(client, bucket, file_key)
    with db.auto_commit():
        cos_data = {
            "file_name": name,
            "file_key": file_key,
            "file_md5": file_md5,
            "file_size": size,
            "status": "UPLOADED",
            "commit": True,
        }
        if lin_config.get_config("cos.need_save_url"):
            cos_data["url"] = permanent_url
        one = COS.create(**cos_data)
        cos_id = one.id
    return file_meta(
        id=cos_id,
        key=key,
        url=resolve_url(file_key, permanent_url),
        file_name=name,
        file_key=file_key,
        size=size,
    )


def get_cos_client():
    from qcloud_cos import CosConfig, CosS3Client

    token, proxies, endpoint, domain = None, None, None, None
    secret_id = lin_config.get_config("cos.access_key_id")
    secret_key = lin_config.get_config("cos.access_key_secret")
    region = lin_config.get_config("cos.region")
    scheme = lin_config.get_config("cos.scheme")
    if lin_config.get_config("cos.token"):
        token = lin_config.get_config("cos.token")
    if lin_config.get_config("cos.proxies"):
        proxies = lin_config.get_config("cos.proxies")
    if lin_config.get_config("cos.endpoint"):
        endpoint = lin_config.get_config("cos.endpoint")
    if lin_config.get_config("cos.domain"):
        domain = lin_config.get_config("cos.domain")

    config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key, Scheme=scheme)
    if token:
        config._token = token
    if proxies:
        config._proxies = proxies
    if endpoint:
        config._endpoint = endpoint
    if domain:
        config._domain = domain
    return CosS3Client(config)


def allowed_file(filename):
    return extension_allowed(filename, lin_config.get_config("cos.allowed_extensions", []))
