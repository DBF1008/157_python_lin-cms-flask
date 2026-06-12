from typing import List, Optional

from pydantic import RootModel

from app.lin import BaseModel


class CosOutSchema(BaseModel):
    key: Optional[str] = None
    id: int
    name: str
    path: str
    url: str
    size: Optional[int] = None
    extension: str
    md5: str
    type: str
    file_name: str
    file_key: str


class CosOutSchemaList(RootModel[List[CosOutSchema]]):
    pass
