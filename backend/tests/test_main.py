from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_read_item() -> None:
    response = client.post("/items/", json={"name": "Plumbus"})
    assert response.status_code == 201
    item = response.json()
    assert item["name"] == "Plumbus"

    response = client.get(f"/items/{item['id']}")
    assert response.status_code == 200
    assert response.json() == item

    response = client.get("/items/")
    assert item in response.json()


def test_create_item_requires_a_name() -> None:
    response = client.post("/items/", json={"name": ""})
    assert response.status_code == 422


def test_read_missing_item() -> None:
    response = client.get("/items/0")
    assert response.status_code == 404
    assert response.json() == {"detail": "Item not found"}
