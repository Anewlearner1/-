from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.routers import analysis


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Supply Chain Intelligence API", lifespan=lifespan)

app.include_router(analysis.router)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env}
