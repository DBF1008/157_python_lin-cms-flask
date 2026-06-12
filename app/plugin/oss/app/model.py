from sqlalchemy import Column, Integer, String

from app.lin import BaseCrud


class OSS(BaseCrud):
    __tablename__ = "oss"

    id = Column(Integer, primary_key=True)
    url = Column(String(255), nullable=False)
    file_name = Column(String(255), nullable=True)
    file_key = Column(String(255), nullable=True, comment="OSS 对象存储 key")
    file_md5 = Column(String(40), nullable=True, comment="md5值，防止上传重复文件")
    file_size = Column(Integer(), nullable=True)
