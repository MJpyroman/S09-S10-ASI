"""Application Flask de demonstration — pipeline CI/CD blue/green."""
import os

import redis
from flask import Flask, jsonify

app = Flask(__name__)

ALERT_THRESHOLD = 25

SERVICE_NAME = "projet-devops-groupe-demo"
VERSION = "1.0"

# Couleur du slot blue/green dans lequel ce conteneur tourne (etape 3).
DEPLOY_COLOR = os.getenv("DEPLOY_COLOR", "local")

# SHA du commit reellement deploye, injecte au deploiement (etape 8).
# C'est ce champ que le smoke test de deploy.sh compare a github.sha.
COMMIT_SHA = os.getenv("APP_COMMIT_SHA", "unknown")


def alert_threshold():
    """Seuil d'alerte au-dessus duquel une notification est declenchee."""
    return ALERT_THRESHOLD


def sanitize_input(value):
    """Echappe les caracteres dangereux d'une entree utilisateur."""
    return value.replace("<", "&lt;").replace(">", "&gt;")


def get_redis_client():
    """Client Redis : l'hote est le NOM DU SERVICE compose, pas localhost.

    Les timeouts courts sont volontaires : /health doit repondre vite,
    y compris quand Redis est injoignable.
    """
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )


@app.route("/health")
def health():
    """Healthcheck reel : verifie la dependance Redis (etape 1).

    Renvoie 503 des que le PING echoue. Sans ca, deploy.sh promouvrait
    une version cassee exactement comme une version saine : le rollback
    automatique serait purement decoratif.
    """
    try:
        if not get_redis_client().ping():
            raise redis.RedisError("PING Redis negatif")
    except Exception as exc:
        return jsonify(status="error", redis="down", reason=str(exc)), 503
    return jsonify(status="ok", redis="up"), 200


@app.route("/status")
def status():
    """Identite de la version en cours d'execution.

    deploy_color : dans quel slot blue/green on tourne.
    commit_sha   : quel commit tourne reellement (verifie avant bascule).
    """
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_DEBUG") == "1")
