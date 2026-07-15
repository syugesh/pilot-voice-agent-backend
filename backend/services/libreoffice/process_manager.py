"""
process_manager.py
==================
Coordinates startup/shutdown of:
  1. LibreOfficeDaemon  — persistent headless soffice process
  2. UNOWorkerClient    — Python 3.12 IPC worker subprocess

Call `startup()` from your FastAPI lifespan handler on app start,
and `shutdown()` on app stop.
"""

from __future__ import annotations
import asyncio
import logging
import threading

from .libreoffice_daemon import LibreOfficeDaemon
from .uno_client import UNOWorkerClient, get_uno_client

logger = logging.getLogger("lo_process_manager")

_daemon: LibreOfficeDaemon | None = None
_watchdog_thread: threading.Thread | None = None
_shutdown_event = threading.Event()


def startup(uno_port: int = 2002):
    """
    Start the LibreOffice daemon and the UNO worker.
    Call once from FastAPI's lifespan on_startup handler.
    """
    global _daemon, _watchdog_thread

    # 1. Start the headless soffice daemon
    _daemon = LibreOfficeDaemon(port=uno_port)
    _daemon.start()
    logger.info(f"LibreOffice daemon is running on UNO port {uno_port}.")

    # 2. Start the uno_worker.py subprocess (Python 3.12)
    client = get_uno_client()
    client.start_worker()
    logger.info("UNO worker subprocess is running and connected to IPC socket.")

    # 3. Start a background watchdog thread
    _shutdown_event.clear()
    _watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True)
    _watchdog_thread.start()
    logger.info("LibreOffice watchdog thread started.")


def shutdown():
    """
    Gracefully stop the UNO worker and soffice daemon.
    Call from FastAPI's lifespan on_shutdown handler.
    """
    global _daemon, _watchdog_thread

    _shutdown_event.set()
    if _watchdog_thread:
        _watchdog_thread.join(timeout=5.0)
        _watchdog_thread = None

    client = get_uno_client()
    client.stop_worker()
    logger.info("UNO worker subprocess stopped.")

    if _daemon:
        _daemon.stop()
        logger.info("LibreOffice daemon stopped.")
        _daemon = None


def _watchdog_loop():
    """
    Periodically checks that both the soffice socket and the worker
    subprocess are alive. Restarts them if they have crashed.
    """
    while not _shutdown_event.wait(timeout=10.0):
        try:
            # Check soffice daemon
            if _daemon and not _daemon.is_alive():
                logger.warning("Watchdog: soffice daemon died. Restarting…")
                try:
                    _daemon.start()
                except Exception as e:
                    logger.error(f"Watchdog: failed to restart soffice daemon: {e}")

            # Check the uno_worker subprocess
            client = get_uno_client()
            if client._worker_proc and client._worker_proc.poll() is not None:
                logger.warning("Watchdog: UNO worker subprocess exited. Restarting…")
                try:
                    client.start_worker()
                except Exception as e:
                    logger.error(f"Watchdog: failed to restart UNO worker: {e}")

        except Exception as e:
            logger.error(f"Watchdog loop error: {e}")
