import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.analysis import AnalysisResult, Base
from app.routers.analyses import require_auth
from app.database import get_db

TEST_DB = "sqlite:///:memory:"

client = TestClient(app)


@pytest.fixture
def db_engine():
    engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def authed_client(db_engine):
    Session = sessionmaker(bind=db_engine)

    def _get_test_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_test_db
    app.dependency_overrides[require_auth] = lambda: "test-user-id"
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


def test_save_analysis_result(db):
    result = AnalysisResult(
        user_id="user-123",
        product="iPhone 15 Pro",
        tree_json='{"nodes": [], "edges": []}',
    )
    db.add(result)
    db.commit()
    found = db.query(AnalysisResult).filter_by(product="iPhone 15 Pro").first()
    assert found is not None
    assert found.user_id == "user-123"


def test_analysis_has_created_at(db):
    r = AnalysisResult(user_id="u1", product="X", tree_json="{}")
    db.add(r)
    db.commit()
    assert r.created_at is not None


def test_analysis_id_is_uuid(db):
    r = AnalysisResult(user_id="u1", product="Y", tree_json="{}")
    db.add(r)
    db.commit()
    assert len(str(r.id)) == 36


def test_save_endpoint_requires_auth():
    response = client.post(
        "/api/v1/analyses/save",
        json={"product": "HBM", "tree_json": "{}"},
    )
    assert response.status_code == 401


def test_get_analysis_requires_auth():
    response = client.get("/api/v1/analyses/abc123")
    assert response.status_code == 401


def test_save_endpoint_persists_to_db(authed_client):
    response = authed_client.post(
        "/api/v1/analyses/save",
        json={"product": "HBM Memory", "tree_json": '{"nodes":[],"edges":[]}'},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["product"] == "HBM Memory"
    assert "id" in data
