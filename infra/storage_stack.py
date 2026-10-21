"""Results bucket and analyses table. Everything is set to delete on `cdk destroy`."""
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from constructs import Construct


class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.results_bucket = s3.Bucket(
            self,
            "ResultsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                # Demo outputs are disposable; keep storage cost near zero.
                s3.LifecycleRule(prefix="analyses/", expiration=Duration.days(30))
            ],
        )

        self.table = dynamodb.TableV2(
            self,
            "AnalysesTable",
            partition_key=dynamodb.Attribute(
                name="analysis_id", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
