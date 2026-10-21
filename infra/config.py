"""Deploy-time settings read from CDK context (cdk.json or -c key=value)."""
from dataclasses import dataclass
from typing import Optional

from constructs import Construct


@dataclass(frozen=True)
class AppConfig:
    bedrock_model_id: str
    budget_email: Optional[str]
    budget_limit_usd: float

    @classmethod
    def from_context(cls, scope: Construct) -> "AppConfig":
        node = scope.node
        return cls(
            bedrock_model_id=node.try_get_context("bedrockModelId"),
            budget_email=node.try_get_context("budgetEmail"),
            budget_limit_usd=float(node.try_get_context("budgetLimitUsd") or 20),
        )
