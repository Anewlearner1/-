from typing import Literal

from pydantic import BaseModel, field_validator


class AnalysisRequest(BaseModel):
    product: str

    @field_validator("product")
    @classmethod
    def product_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("product must not be blank")
        return v.strip()


class Node(BaseModel):
    id: str
    label: str
    tier: int
    country: str
    risk_level: Literal["low", "medium", "high"]
    replaceability: Literal["low", "medium", "high"]
    description: str
    role: str


class Edge(BaseModel):
    source: str
    target: str


class AnalysisResponse(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
