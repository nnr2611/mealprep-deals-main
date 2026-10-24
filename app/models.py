from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    name: str = Field(description="Generic ingredient name, e.g. 'chicken thighs', 'rolled oats'")
    quantity: float
    unit: str = Field(description="g, kg, ml, l, tsp, tbsp, cup, or 'each'")


class Recipe(BaseModel):
    name: str
    servings: int
    ingredients: list[Ingredient]
    steps: list[str]


class BookRecipes(BaseModel):
    recipes: list[Recipe]


class PlannedMeal(BaseModel):
    day: str
    meal: str = Field(description="breakfast, lunch or dinner")
    recipe_id: int
    servings: int


class MealPlan(BaseModel):
    meals: list[PlannedMeal]
    prep_schedule: list[str] = Field(description="Ordered batch-cooking steps for the prep session")
    summary: str
