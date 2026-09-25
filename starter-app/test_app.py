import pytest

import app as app_module


class FakeRedis:
    """Double de test : `up=False` simule un Redis injoignable."""

    def __init__(self, up=True):
        self.up = up

    def ping(self):
        if not self.up:
            raise ConnectionError("Error 111 connecting to redis:6379.")
        return True

    def incr(self, key):
        return 42


@pytest.fixture
def client():
    return app_module.app.test_client()


def use_redis(monkeypatch, up=True):
    monkeypatch.setattr(app_module, "get_redis_client", lambda: FakeRedis(up))


def test_alert_threshold():
    assert app_module.alert_threshold() == 25


def test_sanitize_input_escapes_html():
    assert app_module.sanitize_input("<script>") == "&lt;script&gt;"


def test_health_ok_when_redis_up(monkeypatch, client):
    use_redis(monkeypatch, up=True)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["redis"] == "up"


def test_health_503_when_redis_down(monkeypatch, client):
    """Le chemin qui rend le rollback automatique possible (etape 1)."""
    use_redis(monkeypatch, up=False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.get_json()["redis"] == "down"


def test_visits(monkeypatch, client):
    use_redis(monkeypatch, up=True)
    response = client.get("/visits")
    assert response.status_code == 200
    assert response.get_json() == {"visits": 42}


def test_status_exposes_color_and_commit_sha(monkeypatch, client):
    """Les deux champs verifies par le smoke test de deploy.sh."""
    monkeypatch.setattr(app_module, "DEPLOY_COLOR", "green")
    monkeypatch.setattr(app_module, "COMMIT_SHA", "abc123")
    payload = client.get("/status").get_json()
    assert payload["deploy_color"] == "green"
    assert payload["commit_sha"] == "abc123"


def test_simulate_error_returns_500(client):
    """L'endpoint qui alimentera l'alerte de l'etape 7."""
    response = client.get("/simulate-error")
    assert response.status_code == 500


def test_metrics_exposes_counter_and_histogram(monkeypatch, client):
    use_redis(monkeypatch, up=True)
    client.get("/status")
    body = client.get("/metrics").get_data(as_text=True)
    assert "http_requests_total" in body
    assert "http_request_duration_seconds_bucket" in body


def test_metrics_does_not_count_itself(client):
    """Sans la garde sur /metrics, chaque scrape fausserait le compteur."""
    client.get("/metrics")
    body = client.get("/metrics").get_data(as_text=True)
    assert 'endpoint="/metrics"' not in body
