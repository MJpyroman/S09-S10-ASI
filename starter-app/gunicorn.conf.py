"""Configuration gunicorn.

Le hook child_exit supprime les fichiers de metriques d'un worker qui
meurt, sinon ses compteurs resteraient agreges indefiniment.
"""
import os

from prometheus_client import multiprocess


def child_exit(server, worker):
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(worker.pid)
