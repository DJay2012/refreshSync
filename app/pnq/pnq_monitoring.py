import logging
import os
from typing import Optional, Dict

from app.pnq.pnq_heartbeat import Heartbeat

logger = logging.getLogger("pnq_monitoring")


def create_heartbeat_from_env() -> Heartbeat:
    """
    Create and start a Heartbeat using only environment variables.

    Required / recommended env vars:
      - PNQ_SERVICE_NAME                (required)
      - PNQ_MONITOR_REDIS_HOST          (required)
      - PNQ_MONITOR_REDIS_PORT          (optional, default 6379)
      - PNQ_MONITOR_REDIS_DB            (optional, default 0)
      - PNQ_MONITOR_REDIS_PASSWORD      (optional)
      - PNQ_MONITOR_INTERVAL_SECONDS    (optional, default 60)
      - PNQ_MONITOR_NAMESPACE           (optional, default "pnq:heartbeat")
      - PNQ_SERVICE_TAG                 (optional, per-process tag like "api")
      - PNQ_SERVER_NAME                 (optional, logical server name; falls back to hostname)
      - PNQ_DEPLOYMENT_ENV              (optional; sent as deployment_env in JSON)
      - ENV / ENVIRONMENT / APP_ENV / NODE_ENV (optional aliases for deployment_env)
    """
    service_name = os.getenv("PNQ_SERVICE_NAME")
    if not service_name:
        raise RuntimeError("PNQ_SERVICE_NAME is not set in environment")

    redis_host = os.getenv("PNQ_MONITOR_REDIS_HOST", "localhost")
    redis_port = int(os.getenv("PNQ_MONITOR_REDIS_PORT", "6379"))
    redis_db = int(os.getenv("PNQ_MONITOR_REDIS_DB", "0"))
    redis_password = os.getenv("PNQ_MONITOR_REDIS_PASSWORD") or None

    interval_seconds = int(os.getenv("PNQ_MONITOR_INTERVAL_SECONDS", "60"))
    namespace = os.getenv("PNQ_MONITOR_NAMESPACE", "pnq:heartbeat")

    service_tag = os.getenv("PNQ_SERVICE_TAG") or None
    server_name = os.getenv("PNQ_SERVER_NAME") or None

    hb = Heartbeat(
        service_name=service_name,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
        interval_seconds=interval_seconds,
        namespace=namespace,
        server_name=server_name,
        service_tag=service_tag,
    )
    hb.start()
    return hb


def attach_fastapi_heartbeat(app, required: bool = False) -> None:
    """
    Attach startup/shutdown handlers to a FastAPI app.

    Usage in app/main.py:
        from fastapi import FastAPI
        from app.pnq.pnq_monitoring import attach_fastapi_heartbeat

        app = FastAPI()
        attach_fastapi_heartbeat(app)
    """
    hb_ref: Dict[str, Optional[Heartbeat]] = {"hb": None}

    @app.on_event("startup")
    async def _startup():
        try:
            hb_ref["hb"] = create_heartbeat_from_env()
        except Exception as e:
            logger.warning("PNQ heartbeat not started (app continues): %s", e)
            if required:
                raise
            hb_ref["hb"] = None

    @app.on_event("shutdown")
    async def _shutdown():
        hb = hb_ref["hb"]
        if hb is not None:
            try:
                hb.stop()
            except Exception as e:
                logger.debug("Heartbeat shutdown: %s", e)


def start_heartbeat() -> Heartbeat:
    """
    Start heartbeat from env; for non-FastAPI scripts.

    Usage:
        from app.pnq.pnq_monitoring import start_heartbeat

        hb = start_heartbeat()
        ... your code ...
        hb.update(processed_count=123, error_count=1)
        hb.stop()
    """
    return create_heartbeat_from_env()


def start_heartbeat_if_configured() -> Optional[Heartbeat]:
    """
    Start heartbeat only if PNQ_SERVICE_NAME (and Redis settings) are configured.

    Returns:
        A started Heartbeat instance if configured, otherwise None.
    """
    try:
        return create_heartbeat_from_env()
    except Exception as e:
        logger.warning("PNQ heartbeat skipped: %s", e)
        return None
