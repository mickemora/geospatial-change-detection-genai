"""Analysis pipeline: API -> Step Functions -> scene search -> Fargate change
detection -> Bedrock analyst report.

Idle cost is close to zero: no NAT gateway (Fargate runs in public subnets with
egress only), Lambdas and Step Functions are pay-per-use, and the only compute
that runs for minutes is the Fargate task during an analysis.
"""
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from infra.config import AppConfig

ROOT = Path(__file__).resolve().parent.parent
FUNCTIONS_DIR = ROOT / "pipeline" / "functions"
DETECTOR_DIR = ROOT / "pipeline" / "change_detector"

STAC_API_URL = "https://earth-search.aws.element84.com/v1"


class PipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        results_bucket: s3.IBucket,
        table: dynamodb.ITableV2,
        config: AppConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- Network + Fargate change detector -------------------------------
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )
        # Free gateway endpoint so result uploads stay on the AWS network.
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        detector_logs = logs.LogGroup(
            self,
            "DetectorLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        task_def = ecs.FargateTaskDefinition(
            self,
            "DetectorTask",
            cpu=2048,
            memory_limit_mib=8192,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        container = task_def.add_container(
            "detector",
            image=ecs.ContainerImage.from_asset(
                str(DETECTOR_DIR), platform=ecr_assets.Platform.LINUX_AMD64
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="detector", log_group=detector_logs),
            environment={"RESULTS_BUCKET": results_bucket.bucket_name},
        )
        results_bucket.grant_read_write(task_def.task_role)

        detector_sg = ec2.SecurityGroup(
            self,
            "DetectorSg",
            vpc=vpc,
            description="Change detector task: outbound only (STAC COGs, S3)",
            allow_all_outbound=True,
        )

        # ---- Bedrock guardrail ------------------------------------------------
        guardrail = bedrock.CfnGuardrail(
            self,
            "AnalystGuardrail",
            name=f"{self.stack_name}-analyst",
            description="Keeps analyst reports descriptive and out of targeting territory.",
            blocked_input_messaging="This request is outside the scope of the change detection analyst.",
            blocked_outputs_messaging="The generated report was withheld by the guardrail.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=t, input_strength="HIGH", output_strength="HIGH"
                    )
                    for t in ("HATE", "INSULTS", "SEXUAL", "MISCONDUCT")
                ]
                + [
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
                    )
                ]
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="TargetingAndStrikePlanning",
                        type="DENY",
                        definition=(
                            "Recommending locations or assets as targets, planning strikes or attacks, "
                            "or advising on weapons employment against a site."
                        ),
                        examples=[
                            "Which building at this site should be struck first?",
                            "Recommend aim points for an attack on this facility.",
                        ],
                    )
                ]
            ),
        )
        guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "AnalystGuardrailVersion",
            guardrail_identifier=guardrail.attr_guardrail_id,
            description="Initial version",
        )

        # ---- Lambdas ------------------------------------------------------------
        common_env = {
            "TABLE_NAME": table.table_name,
            "RESULTS_BUCKET": results_bucket.bucket_name,
        }

        search_fn = self._function(
            "SceneSearchFn",
            "scene_search",
            timeout=Duration.seconds(60),
            environment={**common_env, "STAC_API_URL": STAC_API_URL},
        )
        table.grant_read_write_data(search_fn)
        results_bucket.grant_put(search_fn)

        report_fn = self._function(
            "ReportFn",
            "report",
            timeout=Duration.seconds(120),
            memory_size=512,
            environment={
                **common_env,
                "MODEL_ID": config.bedrock_model_id,
                "GUARDRAIL_ID": guardrail.attr_guardrail_id,
                "GUARDRAIL_VERSION": guardrail_version.attr_version,
            },
        )
        table.grant_read_write_data(report_fn)
        results_bucket.grant_read_write(report_fn)
        report_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    # Cross-region inference profiles route to the model in several regions.
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                ],
            )
        )
        report_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[guardrail.attr_guardrail_arn],
            )
        )

        # ---- Step Functions workflow ------------------------------------------
        mark_failed = tasks.DynamoUpdateItem(
            self,
            "MarkFailed",
            table=table,
            key={
                "analysis_id": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$.analysis_id")
                )
            },
            update_expression="SET #s = :s, error_message = :e, updated_at = :t",
            expression_attribute_names={"#s": "status"},
            expression_attribute_values={
                ":s": tasks.DynamoAttributeValue.from_string("FAILED"),
                ":e": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.json_to_string(sfn.JsonPath.object_at("$.error"))
                ),
                ":t": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$$.State.EnteredTime")
                ),
            },
            result_path=sfn.JsonPath.DISCARD,
        ).next(sfn.Fail(self, "AnalysisFailed"))

        search = tasks.LambdaInvoke(
            self,
            "SearchScenes",
            lambda_function=search_fn,
            payload_response_only=True,
            result_path="$.scenes",
        ).add_catch(mark_failed, result_path="$.error")

        detect = tasks.EcsRunTask(
            self,
            "DetectChanges",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            cluster=cluster,
            task_definition=task_def,
            launch_target=tasks.EcsFargateLaunchTarget(
                platform_version=ecs.FargatePlatformVersion.LATEST
            ),
            container_overrides=[
                tasks.ContainerOverride(
                    container_definition=container,
                    environment=[
                        tasks.TaskEnvironmentVariable(
                            name="ANALYSIS_ID", value=sfn.JsonPath.string_at("$.analysis_id")
                        )
                    ],
                )
            ],
            assign_public_ip=True,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[detector_sg],
            task_timeout=sfn.Timeout.duration(Duration.minutes(20)),
            result_path=sfn.JsonPath.DISCARD,
        ).add_catch(mark_failed, result_path="$.error")

        report = tasks.LambdaInvoke(
            self,
            "GenerateReport",
            lambda_function=report_fn,
            payload_response_only=True,
            result_path="$.report",
        ).add_catch(mark_failed, result_path="$.error")

        definition = search.next(detect).next(report).next(sfn.Succeed(self, "Done"))

        state_machine = sfn.StateMachine(
            self,
            "AnalysisStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(30),
        )

        # ---- REST API -----------------------------------------------------------
        api_fn = self._function(
            "ApiFn",
            "api",
            timeout=Duration.seconds(15),
            environment={**common_env, "STATE_MACHINE_ARN": state_machine.state_machine_arn},
        )
        table.grant_read_write_data(api_fn)
        results_bucket.grant_read(api_fn)
        state_machine.grant_start_execution(api_fn)

        api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=api_fn,
            proxy=True,
            cloud_watch_role=False,
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                # Public demo endpoint: throttle hard so nobody can run up the bill.
                throttling_rate_limit=2,
                throttling_burst_limit=5,
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["GET", "POST", "OPTIONS"],
            ),
        )
        self.api_url = api.url

        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)

    def _function(
        self,
        construct_id: str,
        folder: str,
        *,
        timeout: Duration,
        environment: dict,
        memory_size: int = 256,
    ) -> lambda_.Function:
        log_group = logs.LogGroup(
            self,
            f"{construct_id}Logs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        return lambda_.Function(
            self,
            construct_id,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(FUNCTIONS_DIR / folder)),
            timeout=timeout,
            memory_size=memory_size,
            environment=environment,
            log_group=log_group,
        )
