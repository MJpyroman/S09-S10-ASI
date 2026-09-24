#!/usr/bin/env bash
#
# deploy.sh — deploiement blue/green avec rollback automatique (etapes 4 & 8).
#
# Principe :
#   1. lire la couleur active dans un etat persiste ;
#   2. demarrer la nouvelle version dans la couleur INACTIVE ;
#   3. attendre /health (retries espaces, pas un test unique) ;
#   4. smoke test sur /status : bonne couleur ET bon commit ;
#   5. succes -> reecrire la conf nginx, `nginx -s reload`, PUIS arreter
#      l'ancienne couleur (cet ordre evite de couper le trafic) ;
#      echec  -> detruire la tentative, l'etat de prod reste INCHANGE.
#
# Variables d'environnement :
#   APP_IMAGE       image a deployer          (defaut : s09-s10-app:local)
#   APP_COMMIT_SHA  SHA injecte dans /status  (defaut : git rev-parse HEAD)
#   EXPECTED_SHA    SHA exige par le smoke test (defaut : APP_COMMIT_SHA)
#   HEALTH_RETRIES  nombre de tentatives      (defaut : 30)
#   HEALTH_DELAY    secondes entre tentatives (defaut : 2)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

STATE_FILE="$SCRIPT_DIR/state/active_color"
NGINX_ACTIVE_FILE="$REPO_DIR/nginx/active/upstream.conf"

APP_COMMIT_SHA="${APP_COMMIT_SHA:-$(git rev-parse HEAD 2>/dev/null || echo dev)}"
EXPECTED_SHA="${EXPECTED_SHA:-$APP_COMMIT_SHA}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_DELAY="${HEALTH_DELAY:-2}"
export APP_COMMIT_SHA
export APP_IMAGE="${APP_IMAGE:-s09-s10-app:local}"

port_of() {
    case "$1" in
        blue)  echo 5001 ;;
        green) echo 5002 ;;
        *)     echo "couleur inconnue: $1" >&2; return 1 ;;
    esac
}

log()  { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] ECHEC: %s\n' "$*" >&2; }

read_active_color() {
    [[ -f "$STATE_FILE" ]] || return 0          # environnement neuf
    tr -d '[:space:]' < "$STATE_FILE"
}

wait_for_health() {
    local color="$1" port attempt
    port="$(port_of "$color")"
    for ((attempt = 1; attempt <= HEALTH_RETRIES; attempt++)); do
        if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
            log "/health OK sur $color (tentative $attempt/$HEALTH_RETRIES)"
            return 0
        fi
        sleep "$HEALTH_DELAY"
    done
    fail "/health n'a jamais repondu 200 sur $color apres $HEALTH_RETRIES tentatives"
    curl -sS --max-time 3 "http://127.0.0.1:${port}/health" || true
    return 1
}

smoke_test() {
    local color="$1" port body got_color got_sha
    port="$(port_of "$color")"

    if ! body="$(curl -fsS --max-time 3 "http://127.0.0.1:${port}/status")"; then
        fail "/status injoignable sur $color"
        return 1
    fi
    log "/status ($color) : $body"

    got_color="$(jq -r '.deploy_color // empty' <<<"$body")"
    got_sha="$(jq -r '.commit_sha // empty' <<<"$body")"

    if [[ "$got_color" != "$color" ]]; then
        fail "deploy_color='$got_color' alors que '$color' etait attendu"
        return 1
    fi
    if [[ "$got_sha" != "$EXPECTED_SHA" ]]; then
        fail "commit_sha='$got_sha' alors que '$EXPECTED_SHA' etait attendu"
        return 1
    fi
    log "smoke test OK : couleur=$got_color commit=$got_sha"
}

switch_traffic() {
    local color="$1"
    mkdir -p "$(dirname "$NGINX_ACTIVE_FILE")"
    # $active_app est une variable NGINX : elle doit rester litterale,
    # d'ou les quotes simples volontaires.
    # shellcheck disable=SC2016
    printf 'set $active_app "app-%s";\n' "$color" > "$NGINX_ACTIVE_FILE"
    docker compose exec -T nginx nginx -t
    docker compose exec -T nginx nginx -s reload
    log "nginx route desormais vers app-$color"
}

teardown() {
    local color="$1"
    docker compose --profile "$color" rm --stop --force "app-$color" >/dev/null 2>&1 || true
}

main() {
    local active target
    active="$(read_active_color)"

    if [[ -z "$active" ]]; then
        active="aucune"
        target="blue"
    elif [[ "$active" == "blue" ]]; then
        target="green"
    else
        target="blue"
    fi

    log "couleur active : $active  ->  deploiement vers : $target"
    log "image          : $APP_IMAGE"
    log "commit attendu : $EXPECTED_SHA"

    docker compose up -d redis nginx

    log "demarrage de app-$target..."
    docker compose --profile "$target" up -d app-"$target"

    if ! wait_for_health "$target" || ! smoke_test "$target"; then
        fail "la version candidate est rejetee, rollback automatique"
        teardown "$target"
        log "trafic inchange : la couleur active reste '$active'"
        docker compose ps
        exit 1
    fi

    switch_traffic "$target"
    echo "$target" > "$STATE_FILE"

    if [[ "$active" != "aucune" ]]; then
        log "arret de l'ancienne couleur ($active)"
        teardown "$active"
    fi

    log "deploiement termine : couleur active = $target"
    docker compose ps
}

main "$@"
