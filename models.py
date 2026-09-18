from pydantic import BaseModel
from typing import Optional


class StockCreate(BaseModel):
    code: str
    up_warning: Optional[float] = None
    down_warning: Optional[float] = None
    buy_price: Optional[float] = None


class StockUpdate(BaseModel):
    """全量覆盖式更新：前端传完整字段，None 即为清空"""
    up_warning: Optional[float] = None
    down_warning: Optional[float] = None
    buy_price: Optional[float] = None


class TabCreate(BaseModel):
    name: str
