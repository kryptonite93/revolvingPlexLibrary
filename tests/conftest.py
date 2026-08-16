from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.persistence.database import Base
from app.settings import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_environment="test",
        app_secret="test-secret-that-is-long-enough-for-signed-sessions",
        config_directory=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'test.db').as_posix()}",
        secure_cookies=False,
    )


@pytest.fixture
def app(test_settings: Settings):
    application = create_app(test_settings)
    Base.metadata.create_all(application.state.database.engine)
    yield application
    application.state.database.engine.dispose()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
