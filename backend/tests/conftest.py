import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DISTILL_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    from distill.api import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def demo(client):
    r = client.post("/api/demo")
    assert r.status_code == 201
    return client
