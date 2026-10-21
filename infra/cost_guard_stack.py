"""Optional monthly AWS Budgets alert. Deployed only when -c budgetEmail=you@example.com."""
from aws_cdk import Stack
from aws_cdk import aws_budgets as budgets
from constructs import Construct


class CostGuardStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        email: str,
        monthly_limit_usd: float,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        subscribers = [
            budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=email)
        ]

        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="geospatial-change-detection-genai",
                budget_type="COST",
                time_unit="MONTHLY",
                # Account-wide on purpose: a tag filter only works after the tag is
                # activated for cost allocation, and a silent $0 budget is worse than none.
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=monthly_limit_usd, unit="USD"
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=notification_type,
                        comparison_operator="GREATER_THAN",
                        threshold=threshold,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=subscribers,
                )
                for notification_type, threshold in (("ACTUAL", 80), ("FORECASTED", 100))
            ],
        )
