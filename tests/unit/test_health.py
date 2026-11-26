def test_health_retorna_503_sem_banco():
    from fastapi.testclient import TestClient

    from app.db.session import get_session
    from app.main import app

    class SessaoQuebrada:
        def execute(self, *_):
            raise ConnectionError("banco fora")

    app.dependency_overrides[get_session] = lambda: SessaoQuebrada()
    try:
        resp = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["status"] == "erro"
