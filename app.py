#!/usr/bin/env python3
"""CDK entry point for the geospatial change detection + gen AI demo.

Stacks (deploy and destroy independently):
  GeoChange-Storage    results bucket + analyses table
  GeoChange-Pipeline   VPC, Fargate change detector, Lambdas, Step Functions,
                       Bedrock guardrail, REST API
  GeoChange-Frontend   static map UI on S3 + CloudFront
  GeoChange-CostGuard  optional AWS Budgets alert (only with -c budgetEmail=...)
"""
import os

import aws_cdk as cdk

from infra.config import AppConfig
from infra.cost_guard_stack import CostGuardStack
from infra.frontend_stack import FrontendStack
from infra.pipeline_stack import PipelineStack
from infra.storage_stack import StorageStack

app = cdk.App()
config = AppConfig.from_context(app)

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    # Sentinel-2 COGs live in us-west-2; deploying there keeps reads in-region.
    region=os.getenv("CDK_DEFAULT_REGION", "us-west-2"),
)

storage = StorageStack(app, "GeoChange-Storage", env=env)

pipeline = PipelineStack(
    app,
    "GeoChange-Pipeline",
    results_bucket=storage.results_bucket,
    table=storage.table,
    config=config,
    env=env,
)

FrontendStack(app, "GeoChange-Frontend", api_url=pipeline.api_url, env=env)

if config.budget_email:
    CostGuardStack(
        app,
        "GeoChange-CostGuard",
        email=config.budget_email,
        monthly_limit_usd=config.budget_limit_usd,
        env=env,
    )

cdk.Tags.of(app).add("project", "geospatial-change-detection-genai")

app.synth()
