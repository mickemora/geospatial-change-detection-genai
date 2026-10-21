"""Synth the CDK app and check the properties that make `cdk destroy` clean and
idle cost low. Skipped when aws-cdk-lib is not installed."""
import unittest

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Match, Template
except ImportError:  # pragma: no cover
    cdk = None


@unittest.skipIf(cdk is None, "aws-cdk-lib not installed")
class StackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from infra.config import AppConfig
        from infra.frontend_stack import FrontendStack
        from infra.pipeline_stack import PipelineStack
        from infra.storage_stack import StorageStack

        app = cdk.App()
        env = cdk.Environment(account="123456789012", region="us-west-2")
        config = AppConfig(bedrock_model_id="test-model", budget_email=None, budget_limit_usd=20)
        storage = StorageStack(app, "Storage", env=env)
        pipeline = PipelineStack(app, "Pipeline", results_bucket=storage.results_bucket,
                                 table=storage.table, config=config, env=env)
        frontend = FrontendStack(app, "Frontend", api_url=pipeline.api_url, env=env)
        cls.storage = Template.from_stack(storage)
        cls.pipeline = Template.from_stack(pipeline)
        cls.frontend = Template.from_stack(frontend)

    def test_storage_is_destroyable(self):
        self.storage.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Delete"})
        self.storage.has_resource("AWS::DynamoDB::GlobalTable", {"DeletionPolicy": "Delete"})

    def test_no_nat_gateway(self):
        self.pipeline.resource_count_is("AWS::EC2::NatGateway", 0)

    def test_log_groups_are_destroyable(self):
        for resource in self.pipeline.find_resources("AWS::Logs::LogGroup").values():
            self.assertEqual(resource.get("DeletionPolicy"), "Delete")

    def test_workflow_and_guardrail_exist(self):
        self.pipeline.resource_count_is("AWS::StepFunctions::StateMachine", 1)
        self.pipeline.resource_count_is("AWS::Bedrock::Guardrail", 1)
        self.pipeline.has_resource_properties("AWS::ApiGateway::Stage", {
            "MethodSettings": Match.array_with([
                Match.object_like({"ThrottlingRateLimit": 2, "ThrottlingBurstLimit": 5})
            ])
        })

    def test_frontend_is_private_behind_cloudfront(self):
        self.frontend.resource_count_is("AWS::CloudFront::Distribution", 1)
        self.frontend.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)


if __name__ == "__main__":
    unittest.main()
