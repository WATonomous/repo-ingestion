import json
import jwt
import logging
import os
import re
import requests
import time
from datetime import datetime, timedelta
from fastapi import HTTPException
from pydantic import BaseModel

logger = logging.getLogger()

branch_prefix = "repo-ingestion-"

def set_up_logging():
    log_level = os.environ.get("APP_LOG_LEVEL", "INFO")
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

def get_jwt(app_id, pem_path):
    """
    Get a JWT for GitHub Apps authentication
    Derived from:
    https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation#generating-an-installation-access-token
    """
    with open(pem_path, 'rb') as pem_file:
        signing_key = jwt.jwk_from_pem(pem_file.read())
    
    payload = {
        # Issued at time
        'iat': int(time.time()),
        # JWT expiration time (10 minutes maximum)
        'exp': int(time.time()) + 600,
        # GitHub App's identifier
        'iss': app_id
    }

    # Create JWT
    jwt_instance = jwt.JWT()
    encoded_jwt = jwt_instance.encode(payload, signing_key, alg='RS256')

    return encoded_jwt

github_token_cache = None

def get_github_token():
    global github_token_cache

    if github_token_cache and datetime.strptime(github_token_cache["expires_at"], "%Y-%m-%dT%H:%M:%SZ") - datetime.utcnow() > timedelta(minutes=1):
        logger.debug(f"Using cached token. Expires at {github_token_cache['expires_at']}")
        return github_token_cache["token"]

    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_APP_INSTALLATION_ID"]
    pem_path = os.environ["GITHUB_APP_PRIVATE_KEY_PATH"]

    jwt = get_jwt(app_id, pem_path)

    # Get an access token for the installation
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {jwt}',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    response = requests.post(url, headers=headers)
    response.raise_for_status()

    github_token_cache = response.json()

    logger.debug(f"Generated new token. Expires at {github_token_cache['expires_at']}")
    return github_token_cache["token"]

class File(BaseModel):
    path: str
    content: str

class IngestPayload(BaseModel):
    repo: str
    branch_suffix: str
    files: list[File]

def validate_ingest_payload(payload: IngestPayload):
    for allowed_payload in json.loads(os.environ["ALLOWED_INGEST_PAYLOADS"]):
        if re.match(allowed_payload["repo"], payload.repo) and re.match(allowed_payload["branch_suffix"], payload.branch_suffix):
            for file in payload.files:
                if not re.match(allowed_payload["files"]["path"], file.path) or not re.match(allowed_payload["files"]["content"], file.content):
                    raise HTTPException(status_code=400, detail=f"File {file.path} does not match allowed regex")
            return True
    raise HTTPException(status_code=400, detail=f"Payload does not match allowed regex")

def compare_line_by_line(str1, str2):
    """
    Compare two strings line by line. This is useful for comparing strings that may have different line endings.
    """
    return str1.splitlines() == str2.splitlines()

pr_body_prefix = "<!-- This section is manged by repo-ingestion-bot. Please Do not edit manually! -->"
pr_body_postfix = "<!-- End of section managed by repo-ingestion-bot -->"

def wrap_pr_body(body):
    """
    Wrap the PR body with the prefix and postfix.
    """
    return "\n\n" + pr_body_prefix + "\n" + body + "\n" + pr_body_postfix + "\n\n"

def extract_pr_body(body):
    """
    Extract the PR body from the prefix and postfix.
    """
    if not body or pr_body_prefix not in body or pr_body_postfix not in body:
        return ""

    return body.split(pr_body_prefix)[1].split(pr_body_postfix)[0]

def update_pr_body(body, new_body):
    """
    Update the PR body with the new body.
    """
    if not body:
        return wrap_pr_body(new_body)

    if pr_body_prefix not in body or pr_body_postfix not in body:
        return body + wrap_pr_body(new_body)
    
    return body.split(pr_body_prefix)[0].rstrip() + wrap_pr_body(new_body) + body.split(pr_body_postfix)[1].lstrip()