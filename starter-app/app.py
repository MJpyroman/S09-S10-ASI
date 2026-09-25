"""Application Flask de demonstration — blue/green + metriques Prometheus."""
import os
import time

import redis
from flask import Flask, Response, g, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)

app = Flask(__name__)

ALERT_THRESHOLD = 25

SERVICE_NAME = "projet-devops-groupe-demo"
VERSION = "1.0"

# Couleur du slot blue/green dans lequel ce conteneur tourne (seance 4).
DEPLOY_COLOR = os.getenv("DEPLOY_COLOR", "local")

# SHA du commit reellement deploye, injecte au deploiement (seance 4).
COMMIT_SHA = os.getenv("APP_COMMIT_SHA", "unknown")

METRICS_PATH = "/metrics"

# --- Metriques ------------------------------------------------------------
# Les labels restent de faible cardinalite : `endpoint` porte le motif de
# route (/visits) et non l'URL appelee. Avec l'URL brute, chaque valeur
# distincte creerait une serie temporelle supplementaire.
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Nombre total de requetes HTTP traitees",
    ["method", "endpoint", "status"],
)

# Un histogramme, pas une moyenne : la moyenne masque un endpoint qui
# repond vite la plupart du temps et tres lentement de temps en temps.
# Les buckets sont ce qui permet de calculer un p95 reel.
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Duree de traitement des requetes HTTP",
    ["method", "endpoint"],
)


@app.before_request
def start_timer():
    g.request_start = time.perf_counter()


@app.after_request
def record_request(response):
    """Compte la requete et mesure sa duree.

    /metrics est exclu volontairement : sans cette garde, chaque scrape
    Prometheus incrementerait le compteur, qui finirait par mesurer
    surtout sa propre collecte.
    """
    if request.path == METRICS_PATH:
        return response

    endpoint = request.url_rule.rule if request.url_rule else "unknown"
    REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()

    started = g.pop("request_start", None)
    if started is not None:
        REQUEST_LATENCY.labels(request.method, endpoint).observe(
            time.perf_counter() - started
        )
    return response


def alert_threshold():
    """Seuil d'alerte au-dessus duquel une notification est declenchee."""
    return ALERT_THRESHOLD


def sanitize_input(value):
    """Echappe les caracteres dangereux d'une entree utilisateur."""
    return value.replace("<", "&lt;").replace(">", "&gt;")


def get_redis_client():
    """Client Redis : l'hote est le NOM DU SERVICE compose, pas localhost."""
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )


@app.route(METRICS_PATH)
def metrics():
    """Expose les metriques au format texte Prometheus.

    Chaque worker gunicorn tient son propre registre. Le mode multiprocess
    agrege les fichiers deposes dans PROMETHEUS_MULTIPROC_DIR ; sans lui,
    les scrapes alterneraient entre des compteurs distincts et `rate()`
    verrait des remises a zero permanentes.
    """
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return Response(generate_latest(registry), mimetype=CONTENT_TYPE_LATEST)


@app.route("/health")
def health():
    """Healthcheck reel : verifie la dependance Redis (seance 4)."""
    try:
        if not get_redis_client().ping():
            raise redis.RedisError("PING Redis negatif")
    except Exception as exc:
        return jsonify(status="error", redis="down", reason=str(exc)), 503
    return jsonify(status="ok", redis="up"), 200


@app.route("/status")
def status():
    """Identite de la version en cours d'execution."""
    return jsonify(
        service=SERVICE_NAME,
        version=VERSION,
        deploy_color=DEPLOY_COLOR,
        commit_sha=COMMIT_SHA,
    ), 200


@app.route("/visits")
def visits():
    count = get_redis_client().incr("visits")
    return jsonify(visits=count), 200


@app.route("/simulate-error")
def simulate_error():
    """Renvoie systematiquement une 500, pour declencher l'alerte."""
    return jsonify(status="error", reason="erreur simulee"), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG") == "1")
