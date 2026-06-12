import hashlib

from sqlalchemy import Column, Index, Integer, String, text

from app.lin import BaseCrud, lin_config


class OSS(BaseCrud):
    __tablename__ = "oss"
    __table_args__ = (Index("oss_md5", "file_md5", unique=True),)

    id = Column(Integer, primary_key=True)
    file_name = Column(String(255), nullable=False, server_default="")
    file_key = Column(String(255), nullable=False, server_default="")
    file_md5 = Column(String(40), nullable=False, server_default="", comment="md5 hash for dedup")
    file_size = Column(Integer(), nullable=True)
    extension = Column(String(50), nullable=True)
    type = Column(
        String(10),
        nullable=False,
        server_default=text("'REMOTE'"),
        comment="LOCAL 本地，REMOTE 远程",
    )
    url = Column(String(255), nullable=False)

    @classmethod
    def select_by_md5(cls, md5):
        """Query by MD5 hash for duplicate detection."""
        return cls.query.filter_by(file_md5=md5).first()

    @staticmethod
    def generate_md5(data: bytes) -> str:
        """Generate MD5 hex digest from bytes."""
        md5_obj = hashlib.md5()
        md5_obj.update(data)
        return md5_obj.hexdigest()

    @staticmethod
    def generate_key(filename: str) -> str:
        """Generate a UUID-based object key with lowercase extension."""
        import uuid

        ext = "." + filename.lower().rsplit(".", 1)[-1]
        upload_folder = lin_config.get_config("oss.upload_folder")
        key = str(uuid.uuid1()) + ext
        return upload_folder + "/" + key if upload_folder else key
