# S09-S10-ASI

Pipeline CI/CD bout-en-bout : application Flask + Redis, déployée en
**blue/green** avec rollback automatique.

## Branches

`main` stable, une branche par membre, intégration par Pull Request.
Les PR passent `lint` + `test` ; seul un push sur `main` publie une image
et déploie.

## Application

| Endpoint  | Réponse |
|-----------|---------|
| `/health` | `200` si Redis répond au `PING`, **`503` sinon** |
| `/status` | `service`, `version`, `deploy_color`, `commit_sha` |
| `/visits` | compteur incrémenté dans Redis |

`/health` n'est pas un `200` statique : c'est lui qui permet à `deploy.sh`
de distinguer une version cassée d'une version saine.

## Pipeline

```
lint ──► test ──► build-and-push ──► deploy
```

| Job | Rôle | Déclenchement |
|---|---|---|
| `lint` | `flake8` + `shellcheck` | push + PR |
| `test` | `pytest` (6 tests) | push + PR |
| `build-and-push` | build multi-stage → `ghcr.io` | push sur `main` |
| `deploy` | `deploy.sh` dans `environment: production` | push sur `main` |

Deux tags par build : `ghcr.io/mjpyroman/s09-s10-asi:<sha>` (immuable,
identifie une version précise) et `:latest`.

Points d'échec classiques : `permissions: packages: write` manquant → `403`
sur ghcr.io ; nom d'image en majuscules → refusé, d'où `${GITHUB_REPOSITORY,,}`.
Le runner étant éphémère, le job `deploy` récupère l'image depuis le registre.

## Blue/green

Deux services identiques, un seul actif ; nginx route vers lui.

- `redis` et `nginx` **sans** `profiles` → toujours démarrés.
- `app-blue` / `app-green` sous profil → une seule couleur à la fois.
- nginx cible l'upstream via une **variable** + le résolveur Docker
  (`127.0.0.11`) : avec un nom littéral, il refuserait de démarrer tant que
  la couleur ciblée n'existe pas.
- Couleur active : `deploy/state/active_color`, routage :
  `nginx/active/upstream.conf`.

`deploy/deploy.sh` déploie dans la couleur inactive, attend `/health` en
boucle, passe un smoke test (`deploy_color` **et** `commit_sha`), recharge
nginx (`nginx -s reload`), **puis** arrête l'ancienne version — cet ordre
évite de couper le trafic. En cas d'échec, la couleur active ne bouge pas.

| Variable | Défaut | Rôle |
|---|---|---|
| `APP_IMAGE` | `s09-s10-app:local` | image à déployer |
| `APP_COMMIT_SHA` | `git rev-parse HEAD` | SHA exposé par `/status` |
| `EXPECTED_SHA` | `$APP_COMMIT_SHA` | SHA exigé par le smoke test |
| `HEALTH_RETRIES` / `HEALTH_DELAY` | `30` / `2` | patience du healthcheck |
| `REDIS_HOST` | `redis` | surchargeable pour tester le rollback |

Vérifier le SHA est indispensable : deux déploiements qui ne changent que de
couleur passeraient un test portant sur `deploy_color` seul.

## Utilisation

Prérequis : Docker + Compose, `curl`, `jq`.

```bash
./deploy/deploy.sh                          # 1re exécution : blue
curl -s localhost:8080/status | jq
./deploy/deploy.sh                          # bascule vers green
docker compose --profile blue --profile green down -v
```

Ports : `8080` entrée publique (nginx), `5001`/`5002` accès direct
blue/green — utilisés pour tester une version avant de lui envoyer le trafic.

## Tester le rollback

```bash
# SHA incorrect
EXPECTED_SHA=deadbeef ./deploy/deploy.sh ; echo "exit=$?"

# dépendance cassée → /health en 503
REDIS_HOST=redis-inexistant HEALTH_RETRIES=4 ./deploy/deploy.sh ; echo "exit=$?"

# dans les deux cas : inchangés
cat deploy/state/active_color
curl -s localhost:8080/status | jq
```

Le job `deploy` rejoue ce scénario à chaque run.

## Rollback manuel

Pour un problème découvert **après** un déploiement réussi, on ne touche ni à
l'environnement ni au fichier d'état :

```bash
git log --oneline
git revert <sha>        # relire le diff avant de pousser
git push origin main
```

## Images

Multi-stage : dépendances installées dans un venv (`python:3.12`), seul le
venv est copié dans l'image finale `python:3.12-slim`. `pytest` et `flake8`
sont isolés dans `requirements-dev.txt` et n'entrent jamais dans l'image.

| Image | Base | Taille |
|---|---|---|
| naïve | `python:3.12` | 417 Mo |
| multi-stage | `python:3.12-slim` | **50 Mo** |

Utilisateur non-root (`appuser`), `HEALTHCHECK` branché sur `/health`.

Images publiées : <https://github.com/MJpyroman/S09-S10-ASI/pkgs/container/s09-s10-asi>
