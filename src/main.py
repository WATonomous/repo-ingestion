import logging
from fastapi import FastAPI
from typing import Union
from utils import set_up_logging, get_github_token, logger

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # Set up logging
    set_up_logging()
    logger.info(f"Logging configured with level {logger.level} ({logging.getLevelName(logger.level)})")

@app.get("/")
def read_root():
    return {"Hello": "World", "GITHUB_TOKEN": get_github_token()}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: Union[str, None] = None):
    return {"item_id": item_id, "q": q}