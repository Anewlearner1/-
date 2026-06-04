import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.user import Base, User

TEST_DB = "sqlite:///:memory:"


@pytest.fixture
def db():
    engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_create_user(db):
    user = User(email="test@example.com", name="Test User", provider="google")
    db.add(user)
    db.commit()
    found = db.query(User).filter_by(email="test@example.com").first()
    assert found is not None
    assert found.name == "Test User"


def test_user_email_is_unique(db):
    from sqlalchemy.exc import IntegrityError
    db.add(User(email="dup@example.com", name="A", provider="email"))
    db.commit()
    db.add(User(email="dup@example.com", name="B", provider="email"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_user_has_created_at(db):
    user = User(email="ts@example.com", name="C", provider="email")
    db.add(user)
    db.commit()
    assert user.created_at is not None


def test_user_id_is_uuid(db):
    user = User(email="uuid@example.com", name="D", provider="google")
    db.add(user)
    db.commit()
    assert len(str(user.id)) == 36  # UUID4 format
