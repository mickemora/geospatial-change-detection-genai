"""Static map UI on a private S3 bucket behind CloudFront (origin access control)."""
from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class FrontendStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, api_url: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "SiteDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )

        s3deploy.BucketDeployment(
            self,
            "DeploySite",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.asset(str(FRONTEND_DIR)),
                # API URL is only known at deploy time, so write it into config.js then.
                s3deploy.Source.data("config.js", f'window.APP_CONFIG = {{ apiUrl: "{api_url}" }};\n'),
            ],
            distribution=distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "SiteUrl", value=f"https://{distribution.distribution_domain_name}")
