from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_ok() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_docs_not_exposed() -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
