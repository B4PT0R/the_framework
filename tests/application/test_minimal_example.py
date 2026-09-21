from fastapi.testclient import TestClient

from examples.minimal_agent_app import app


def test_minimal_example_is_an_independent_composed_application():
    headers = {"Authorization": "Bearer example-secret"}

    with TestClient(app) as client:
        unauthorized = client.post(
            "/api/v1/counter/increment", json={"amount": 2},
        )
        incremented = client.post(
            "/api/v1/counter/increment", headers=headers, json={"amount": 2},
        )
        health = client.get("/api/v1/health", headers=headers)

    assert unauthorized.status_code == 401
    assert incremented.json() == {"value": 2}
    assert health.json() == {
        "status": "ok",
        "services": {
            "counter": {
                "status": "running",
                "critical": True,
                "details": {"value": 2},
            },
        },
    }
