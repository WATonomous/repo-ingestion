import logging
from fastapi import FastAPI, HTTPException
from github import Github
from github.GithubException import GithubException
from utils import set_up_logging, get_github_token, logger, IngestPayload, branch_prefix, validate_ingest_payload


app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # Set up logging
    set_up_logging()
    logger.info(f"Logging configured with level {logger.level} ({logging.getLevelName(logger.level)})")

@app.get("/")
def read_root():
    return {"Hello": "World", "GITHUB_TOKEN": get_github_token()}

@app.post("/ingest")
def ingest(payload: IngestPayload):
    """
    Ingests a payload and creates a PR.
    """
    validate_ingest_payload(payload)

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
            # Compare the content to see if we need to update
            if existing_file.decoded_content.decode() == file.content:
                logger.info(f"File {file.path} already exists and is up to date")
            else:
                logger.info(f"File {file.path} already exists but is out of date. Updating...")
                repo.update_file(existing_file.path, f"Create or update {file.path}", file.content, existing_file.sha, branch=branch_name)
        else:
            logger.info(f"File {file.path} does not exist. Creating...")
            repo.create_file(file.path, f"Create or update {file.path}", file.content, branch=branch_name)

    # Create PR
    logger.info(f"Creating PR from {branch_name} to {default_branch.name}...")
    try:
        pr = repo.create_pull(title=f"Update {branch_name}", body=f"Update {branch_name}", head=branch_name, base=default_branch.name)
    except GithubException as e:
        if e.status != 422:
            raise e
        logger.info(f"PR from {branch_name} to {default_branch.name} already exists")
        pr = repo.get_pulls(head=branch_name, base=default_branch.name)[0]

    logger.info(f"GitHub rate limit remaining: {g.rate_limiting[0]} / {g.rate_limiting[1]}")

    return {
        "pr_url": pr.html_url,
    }
