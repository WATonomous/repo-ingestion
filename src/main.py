import json
import logging
import os
import sentry_sdk
import time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from github import Github
from github.GithubException import GithubException
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.crons import monitor
from textwrap import dedent
from utils import (
    set_up_logging,
    get_github_token,
    logger,
    IngestPayload,
    branch_prefix,
    validate_ingest_payload,
    extract_pr_body,
    update_pr_body,
    compare_line_by_line,
    assert_throws,
    transform_file,
)

# BUILD_INFO is generated by the build pipeline (e.g. docker/metadata-action).
# It looks like:
# {"tags":["ghcr.io/watonomous/repo-ingestion:main"],"labels":{"org.opencontainers.image.title":"repo-ingestion","org.opencontainers.image.description":"Simple server to receive file changes and open GitHub pull requests","org.opencontainers.image.url":"https://github.com/WATonomous/repo-ingestion","org.opencontainers.image.source":"https://github.com/WATonomous/repo-ingestion","org.opencontainers.image.version":"main","org.opencontainers.image.created":"2024-01-20T16:10:39.421Z","org.opencontainers.image.revision":"1d55b62b15c78251e0560af9e97927591e260a98","org.opencontainers.image.licenses":""}}
BUILD_INFO=json.loads(os.getenv("DOCKER_METADATA_OUTPUT_JSON", "{}"))
IS_SENTRY_ENABLED = os.getenv("SENTRY_DSN") is not None

# Set up Sentry
if IS_SENTRY_ENABLED:
    build_labels = BUILD_INFO.get("labels", {})
    image_title = build_labels.get("org.opencontainers.image.title", "unknown_image")
    image_version = build_labels.get("org.opencontainers.image.version", "unknown_version")
    image_rev = build_labels.get("org.opencontainers.image.revision", "unknown_rev")

    sentry_config = {
        "dsn": os.environ["SENTRY_DSN"],
        "environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "unknown"),
        "release": os.getenv("SENTRY_RELEASE", f'{image_title}:{image_version}@{image_rev}'),
    }

    print(f"Sentry SDK version: {sentry_sdk.VERSION}")
    print(f"Sentry DSN found. Setting up Sentry with config: {sentry_config}")

    sentry_logging = LoggingIntegration(
        level=logging.INFO,        # Capture info and above as breadcrumbs
        event_level=logging.ERROR  # Send errors as events
    )

    def sentry_traces_sampler(sampling_context):
        # Inherit parent sampling decision
        if sampling_context["parent_sampled"] is not None:
            return sampling_context["parent_sampled"]

        # Don't need to sample health checks
        if sampling_context.get("asgi_scope", {}).get("path", "").startswith("/health"):
            return 0
        
        # Sample everything else
        return 1

    sentry_sdk.init(
        **sentry_config,
        integrations=[sentry_logging],

        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        # We recommend adjusting this value in production,
        # traces_sample_rate=1.0,
        traces_sampler=sentry_traces_sampler,

        enable_tracing=True,
    )
else:
    print("No Sentry DSN found. Skipping Sentry setup.")

app = FastAPI()
state = {
    "sentry_cron_last_ping_time": 0,
    "num_ingest_requests_received": 0,
    "num_ingest_requests_success": 0,
}

# Add CORS for local development. In production, this is handled by the reverse proxy.
origins = [
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    set_up_logging()
    logger.info(f"Logging configured with level {logger.level} ({logging.getLevelName(logger.level)})")

@app.get("/health")
def read_health():
    current_time = time.time()
    # Ping Sentry at least every minute. Using a 30s buffer to be safe.
    if IS_SENTRY_ENABLED and current_time - state["sentry_cron_last_ping_time"] > 30:
        state["sentry_cron_last_ping_time"] = current_time
        ping_sentry()

    return {"status": "ok"}

# Sentry CRON docs: https://docs.sentry.io/platforms/python/crons/
@monitor(monitor_slug='repo-ingestion', monitor_config={
    "schedule": { "type": "interval", "value": 1, "unit": "minute" },
    "checkin_margin": 5, # minutes
    "max_runtime": 1, # minutes
    "failure_issue_threshold": 1,
    "recovery_threshold": 2,
})
def ping_sentry():
    logger.info("Pinged Sentry CRON")

@app.get("/build-info")
def read_build_info():
    return BUILD_INFO

@app.get("/runtime-info")
def read_runtime_info():
    return {
        "sentry_enabled": IS_SENTRY_ENABLED,
        "sentry_sdk_version": sentry_sdk.VERSION,
        "deployment_environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "unknown"),
        "sentry_cron_last_ping_time": state["sentry_cron_last_ping_time"],
        "num_ingest_requests_received": state["num_ingest_requests_received"],
        "num_ingest_requests_success": state["num_ingest_requests_success"],
    }

@app.post("/ingest")
def ingest(payload: IngestPayload):
    """
    Ingests a payload and creates a PR.
    """
    state["num_ingest_requests_received"] += 1

    validate_ingest_payload(payload)

    # Perform transformations
    for file in payload.files:
        transform_file(file)

    g = Github(get_github_token())
    logger.info(f"GitHub rate limit remaining: {g.rate_limiting[0]} / {g.rate_limiting[1]}")
    repo = g.get_repo(payload.repo)

    default_branch = repo.get_branch(repo.default_branch)
    
    # Create branch
    branch_name = f"{branch_prefix}{payload.branch_suffix}"
    logger.info(f"Creating branch {branch_name} from {default_branch.commit.sha}")
    try:
        branch = repo.create_git_ref(f"refs/heads/{branch_name}", default_branch.commit.sha)
    except GithubException as e:
        # 422 is "Reference already exists"
        if e.status != 422:
            raise e
        logger.info(f"Branch {branch_name} already exists")

    # Create/update files
    for file in payload.files:
        logger.info(f"Creating/updating file {file.path}...")
        try:
            existing_file = repo.get_contents(file.path, ref=branch_name)
        except GithubException as e:
            if e.status != 404:
                raise e
            existing_file = None

        if existing_file:
            repo.update_file(existing_file.path, f"Create or update {file.path}", file.content, existing_file.sha, branch=branch_name)
        else:
            logger.info(f"File {file.path} does not exist. Creating...")
            repo.create_file(file.path, f"Create or update {file.path}", file.content, branch=branch_name)

    # Create PR
    pr_head = f"{repo.organization.login}:{branch_name}"
    logger.info(f"Creating PR from {pr_head} to {default_branch.name}...")
    file_list = "".join([f"* {file.path}\n" for file in payload.files])
    pr_title = f"Create or update files: {pr_head}"
    pr_body = dedent(f"""
        ### Introduction

        This PR is automatically generated by the [repo-ingestion](https://github.com/WATonomous/repo-ingestion) service.

        <!-- tags: repo-ingestion -->

        ### Files in the latest submission:

    """) + file_list
    try:
        prs = repo.get_pulls(head=pr_head, base=default_branch.name)
        pr = prs[0]
        assert_throws(lambda: prs[1], IndexError, f"Expected only one PR from {pr_head} to {default_branch.name}, but found more than one")
        if pr.title == pr_title and compare_line_by_line(extract_pr_body(pr.body).strip(), pr_body.strip()):
            logger.info(f"PR from {pr_head} to {default_branch.name} already exists (#{pr.number}) and is up to date")
        else:
            logger.info(f"PR from {pr_head} to {default_branch.name} already exists (#{pr.number}) but is out of date. Updating...")
            pr.edit(title=pr_title, body=update_pr_body(pr.body, pr_body))
    except IndexError:
        logger.info(f"PR from {pr_head} to {default_branch.name} does not exist. Creating...")
        pr = repo.create_pull(title=pr_title, body=update_pr_body("", pr_body), head=pr_head, base=default_branch.name)

    logger.info(f"GitHub rate limit remaining: {g.rate_limiting[0]} / {g.rate_limiting[1]}")

    state["num_ingest_requests_success"] += 1

    return {
        "pr_url": pr.html_url,
    }
