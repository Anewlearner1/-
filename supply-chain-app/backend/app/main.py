from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Future: initialize DB connection pool here
    yield
    # Future: close DB connection pool here


app = FastAPI(title="Supply Chain Intelligence API", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env}
