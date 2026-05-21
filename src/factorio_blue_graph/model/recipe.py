"""Pydantic models for items, recipes, and machines.

`Recipe` mirrors one entry of the packaged `data/recipes.json`. Each entry
in that file has a single output (the top-level `id`), an optional `time`
and `yield` (both `null` for raw resources like iron-ore or crude-oil),
and a list of ingredients. Multi-output recipes are not represented in
the v1 dataset and are intentionally not modelled here.

`Machine` is a stub: the current dataset carries no crafting-speed or
footprint metadata. Phase 2 will populate it from a hand-curated table.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Ingredient(BaseModel):
    item_id: str = Field(alias="id")
    amount: float

    model_config = ConfigDict(populate_by_name=True)


class Recipe(BaseModel):
    item_id: str
    time: float | None
    yield_: float | None = Field(alias="yield")
    ingredients: list[Ingredient]

    model_config = ConfigDict(populate_by_name=True)

    @property
    def is_raw(self) -> bool:
        return self.time is None


class Item(BaseModel):
    id: str
    name: str
    type: str
    category: str
    wiki_link: str | None = None
    recipe: Recipe

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_json_entry(cls, entry: dict) -> Item:
        recipe_data = dict(entry["recipe"])
        recipe = Recipe.model_validate({**recipe_data, "item_id": entry["id"]})
        return cls(
            id=entry["id"],
            name=entry["name"],
            type=entry["type"],
            category=entry["category"],
            wiki_link=entry.get("wiki_link"),
            recipe=recipe,
        )


class Machine(BaseModel):
    """Stub. Phase 2 will load a real machine table."""

    id: str
    crafting_speed: float
    footprint: tuple[int, int]
