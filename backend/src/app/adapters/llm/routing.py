"""Feature routing from `ai/models.yaml`: provider, model, effort, fallback, budget, pricing."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.ports import LlmFeature

ProviderName = Literal["vertex-claude", "vertex-maas", "vertex-gemini"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderName
    model: str = Field(min_length=1, max_length=100)
    effort: Effort = "medium"


class FeatureRoute(ModelChoice):
    fallback: ModelChoice | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=64000)
    daily_budget_usd: float | None = Field(default=None, gt=0)


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(default=0, ge=0)


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tokens: int = Field(default=4096, ge=256)
    daily_budget_usd: float = Field(default=5.0, gt=0)


class RoutingTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    defaults: Defaults = Defaults()
    pricing: dict[str, Price]
    features: dict[LlmFeature, FeatureRoute]

    @model_validator(mode="after")
    def _complete(self) -> RoutingTable:
        missing = set(LlmFeature) - set(self.features)
        if missing:
            raise ValueError(f"models.yaml has no route for: {sorted(missing)}")
        for route in self.features.values():
            for choice in (route, route.fallback):
                if choice is not None and choice.model not in self.pricing:
                    raise ValueError(f"models.yaml has no price for {choice.model}")
        return self

    def route(self, feature: LlmFeature) -> FeatureRoute:
        return self.features[feature]

    def max_tokens(self, feature: LlmFeature) -> int:
        return self.features[feature].max_tokens or self.defaults.max_tokens

    def daily_budget(self, feature: LlmFeature) -> float:
        return self.features[feature].daily_budget_usd or self.defaults.daily_budget_usd

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int, cache_read: int) -> float:
        price = self.pricing.get(model)
        if price is None:
            return 0.0
        per_token = 1 / 1_000_000
        return round(
            (
                input_tokens * price.input
                + output_tokens * price.output
                + cache_read * price.cache_read
            )
            * per_token,
            6,
        )


class RoutingConfigError(ValueError):
    pass


def load_routing(path: Path) -> RoutingTable:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return RoutingTable.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise RoutingConfigError(f"invalid model routing config: {error}") from error
