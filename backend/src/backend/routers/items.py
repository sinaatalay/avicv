from itertools import count

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/items", tags=["items"])


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Item(ItemCreate):
    id: int


# In-memory storage keeps the example dependency-free. It resets on every
# restart and isn't shared between worker processes — swap in a database.
items: dict[int, Item] = {}
next_id = count(1)


@router.get("/")
async def list_items() -> list[Item]:
    return list(items.values())


@router.post("/", status_code=201)
async def create_item(item: ItemCreate) -> Item:
    new_item = Item(id=next(next_id), name=item.name)
    items[new_item.id] = new_item
    return new_item


@router.get("/{item_id}")
async def read_item(item_id: int) -> Item:
    if item_id not in items:
        raise HTTPException(status_code=404, detail="Item not found")
    return items[item_id]
