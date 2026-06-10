"""
PNQ Heartbeat Client
====================
Lightweight heartbeat module for Docker services.
Import this into any Python service to push periodic heartbeats to the central Redis instance.

Docker (optional; for dashboard "Container name" / "Docker image" grouping):
    PNQ_CONTAINER_NAME or CONTAINER_NAME or DOCKER_CONTAINER_NAME — e.g. grokra (compose container_name)
    PNQ_IMAGE or IMAGE or DOCKER_IMAGE or DOCKER_IMAGE_NAME — e.g. grokamazonra:latest

Deployment label (optional; JSON field deployment_env — monitor-core uses for alert routing):
    PNQ_DEPLOYMENT_ENV, or first non-empty of ENV, ENVIRONMENT, APP_ENV, NODE_ENV
    Use production on real deploys; development locally to avoid production alerts.

Usage:
    from pnq_heartbeat import Heartbeat

    # Initialize once at service startup
    hb = Heartbeat(
        service_name="excel-merger",
        redis_host="monitoring-redis.pnq.internal",  # central monitoring Redis
        redis_port=6379,
        redis_db=0,
        interval_seconds=60,  # how often to send heartbeat
    )

    # Start background heartbeat (runs in a daemon thread)
    hb.start()

    # Optionally update status with job-specific metrics
    hb.update(status="processing", processed_count=1500, error_count=2, extra={"current_job": "daily_report"})

    # When shutting down gracefully
    hb.stop()

    # For polling/batch jobs that run and exit (not long-running services):
    hb.beat_once(status="completed", processed_count=5000, duration_seconds=120)
"""

import json
import socket
import threading
import time
import logging
import os
import platform
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("pnq_heartbeat")

# Best-effort cached public IP (so we don't call external services every heartbeat)
_HOST_PUBLIC_IP: str | None = None
_HOST_PUBLIC_IP_LAST_FETCH_TS: float = 0.0


def _best_effort_public_ip() -> str | None:
    """
    Best-effort public IP detection.

    - If PNQ_HOST_PUBLIC_IP is set, use it.
    - Otherwise, query an external endpoint (cached + rate-limited).

    Note: containers behind NAT cannot infer a public IP without external lookup.
    """
    global _HOST_PUBLIC_IP, _HOST_PUBLIC_IP_LAST_FETCH_TS

    explicit = (os.environ.get("PNQ_HOST_PUBLIC_IP") or "").strip() or None
    if explicit:
        return explicit

    refresh_seconds = int(os.environ.get("PNQ_PUBLIC_IP_REFRESH_SECONDS", "21600"))  # 6h
    now_ts = time.time()
    if _HOST_PUBLIC_IP and (now_ts - _HOST_PUBLIC_IP_LAST_FETCH_TS) < refresh_seconds:
        return _HOST_PUBLIC_IP

    # Avoid repeated failures spamming on every beat
    if (now_ts - _HOST_PUBLIC_IP_LAST_FETCH_TS) < max(60, refresh_seconds // 12):
        return _HOST_PUBLIC_IP

    try:
        url = os.environ.get("PNQ_PUBLIC_IP_URL") or "https://api.ipify.org?format=text"
        req = urllib.request.Request(url, headers={"User-Agent": "pnq-monitor-heartbeat"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            txt = resp.read().decode("utf-8", errors="ignore").strip()
            if txt:
                _HOST_PUBLIC_IP = txt
                _HOST_PUBLIC_IP_LAST_FETCH_TS = now_ts
                return _HOST_PUBLIC_IP
    except Exception:
        _HOST_PUBLIC_IP_LAST_FETCH_TS = now_ts
        return _HOST_PUBLIC_IP

    _HOST_PUBLIC_IP_LAST_FETCH_TS = now_ts
    return _HOST_PUBLIC_IP


def _read_deployment_env_for_payload() -> str | None:
    """First non-empty deployment label from common env vars (case preserved; monitor normalizes)."""
    for key in (
        "PNQ_DEPLOYMENT_ENV",
        "ENV",
        "ENVIRONMENT",
        "APP_ENV",
        "NODE_ENV",
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return None


def _sanitize_redis_key_segment(value: str | None, default: str = "default") -> str:
    """Ensure a Redis key segment has no ':' (we use ':' as the delimiter)."""
    t = (value or "").strip()
    if not t:
        return default
    t = t.replace(":", "-").replace(" ", "_")
    return t[:200] if len(t) > 200 else t


class Heartbeat:
    """Pushes periodic heartbeats to a central Redis instance."""

    def __init__(
        self,
        service_name: str,
        redis_host: str = None,
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: str = None,
        interval_seconds: int = 60,
        ttl_seconds: int = None,
        namespace: str = "pnq:heartbeat",
        server_name: str = None,
        service_tag: str = None,
    ):
        """
        Args:
            service_name: Unique name for this service (e.g., "excel-merger", "adminhub-backend")
            redis_host: Central monitoring Redis host. Falls back to PNQ_MONITOR_REDIS_HOST env var.
            redis_port: Redis port. Falls back to PNQ_MONITOR_REDIS_PORT env var.
            redis_db: Redis DB number. Falls back to PNQ_MONITOR_REDIS_DB env var.
            redis_password: Redis password. Falls back to PNQ_MONITOR_REDIS_PASSWORD env var.
            interval_seconds: How often to send heartbeat (default: 60s)
            ttl_seconds: Redis key TTL override. If omitted, uses PNQ_HEARTBEAT_TTL_SECONDS, then
                PNQ_HEARTBEAT_STALE_RETENTION_SECONDS (same as monitor-core), then 3x interval.
                After a container stops, the last heartbeat remains visible until this TTL expires.
            namespace: Redis key prefix for all heartbeat keys.
            server_name: Logical server name for grouping (e.g. "prod-server-1"). Falls back to
                PNQ_SERVER_NAME env var, then to hostname. Use this so many containers on one host
                appear under one server in the dashboard; hostname (container id) is still sent for debugging.
            service_tag: Optional per-process tag (e.g. "api", "subscription-worker") used to
                disambiguate multiple processes for the same service. Falls back to PNQ_SERVICE_TAG.
        """
        self.service_name = service_name
        self.redis_host = redis_host or os.environ.get("PNQ_MONITOR_REDIS_HOST", "localhost")
        self.redis_port = int(redis_port or os.environ.get("PNQ_MONITOR_REDIS_PORT", 6379))
        self.redis_db = int(redis_db or os.environ.get("PNQ_MONITOR_REDIS_DB", 0))
        self.redis_password = redis_password or os.environ.get("PNQ_MONITOR_REDIS_PASSWORD")
        self.interval_seconds = interval_seconds
        # Redis TTL for the main heartbeat key (how long last beat stays visible after container stops).
        # Priority: explicit ttl_seconds arg > PNQ_HEARTBEAT_TTL_SECONDS > PNQ_HEARTBEAT_STALE_RETENTION_SECONDS
        # (same env as monitor-core) > 3 * interval. Min 60s.
        if ttl_seconds is not None:
            self.ttl_seconds = max(60, int(ttl_seconds))
        else:
            env_ttl = (os.environ.get("PNQ_HEARTBEAT_TTL_SECONDS") or "").strip()
            env_retention = (os.environ.get("PNQ_HEARTBEAT_STALE_RETENTION_SECONDS") or "").strip()
            if env_ttl.isdigit():
                self.ttl_seconds = max(60, int(env_ttl))
            elif env_retention.isdigit():
                self.ttl_seconds = max(60, int(env_retention))
            else:
                self.ttl_seconds = max(60, int(interval_seconds * 3))
        self.namespace = namespace

        self._hostname = socket.gethostname()
        self._server_name = (server_name or os.environ.get("PNQ_SERVER_NAME") or "").strip() or self._hostname
        # Stable public IP segment for composite Redis keys (avoid key changing after first fetch).
        self._key_public_ip = (os.environ.get("PNQ_HOST_PUBLIC_IP") or "").strip() or None
        if not self._key_public_ip:
            self._key_public_ip = _best_effort_public_ip() or "unknown"
        self._status = "running"
        self._extra = {}
        self._processed_count = 0
        self._error_count = 0
        self._started_at = datetime.now(timezone.utc).isoformat()
        # service-level identifiers
        self._service_tag = (service_tag or os.environ.get("PNQ_SERVICE_TAG") or "").strip() or None

        self._thread = None
        self._stop_event = threading.Event()
        self._redis = None

    def _get_redis(self):
        """Lazy Redis connection."""
        if self._redis is None:
            try:
                import redis
                self._redis = redis.Redis(
                    host=self.redis_host,
                    port=self.redis_port,
                    db=self.redis_db,
                    password=self.redis_password,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    decode_responses=True,
                )
                # Test connection
                self._redis.ping()
                logger.info(f"Heartbeat connected to Redis at {self.redis_host}:{self.redis_port}")
            except Exception as e:
                logger.error(f"Heartbeat failed to connect to Redis: {e}")
                self._redis = None
                raise
        return self._redis

    def _build_key(self) -> str:
        """Build the Redis key for this service."""
        mode = (os.environ.get("PNQ_HEARTBEAT_KEY_MODE") or "composite").strip().lower()
        if mode in ("legacy", "old", "0", "false"):
            if self._service_tag:
                return f"{self.namespace}:{self.service_name}:{self._service_tag}"
            return f"{self.namespace}:{self.service_name}"

        # Composite (default): public_ip + server_name + service_name + service_tag — unique per host/instance.
        pub = _sanitize_redis_key_segment(self._key_public_ip, "unknown")
        srv = _sanitize_redis_key_segment(self._server_name, "unknown")
        svc = _sanitize_redis_key_segment(self.service_name, "service")
        tag = _sanitize_redis_key_segment(self._service_tag, "default")
        return f"{self.namespace}:{pub}:{srv}:{svc}:{tag}"

    def _build_payload(self) -> dict:
        """Build the heartbeat payload."""
        host_ip = None
        try:
            # Best-effort "host" IP: outbound interface chosen by OS routing.
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                host_ip = s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            try:
                host_ip = socket.gethostbyname(self._hostname)
            except Exception:
                host_ip = None

        os_name = (platform.system() or "").lower() or None
        os_distro = None
        os_distro_version = None
        if os_name == "linux":
            try:
                # Best-effort distro info (e.g. Rocky Linux) if available in container
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    vals = {}
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        v = v.strip().strip('"').strip("'")
                        vals[k.strip()] = v
                os_distro = vals.get("NAME") or vals.get("PRETTY_NAME")
                os_distro_version = vals.get("VERSION_ID") or vals.get("VERSION")
            except Exception:
                os_distro = None
                os_distro_version = None

        arch = platform.machine() or None
        pid = None
        try:
            pid = os.getpid()
        except Exception:
            pid = None

        app_version = (os.environ.get("PNQ_APP_VERSION") or "").strip() or None
        git_sha = (os.environ.get("PNQ_GIT_SHA") or "").strip() or None

        def _first_env(*keys: str) -> str | None:
            """First non-empty env value (Docker-friendly aliases for compose)."""
            for k in keys:
                v = (os.environ.get(k) or "").strip()
                if v:
                    return v
            return None

        # Human-friendly container name (e.g. compose `container_name: grokra`) — not the random hostname.
        container_name = _first_env(
            "PNQ_CONTAINER_NAME",
            "CONTAINER_NAME",
            "DOCKER_CONTAINER_NAME",
        )
        # Docker image ref (e.g. `grokamazonra:latest`).
        image = _first_env(
            "PNQ_IMAGE",
            "IMAGE",
            "DOCKER_IMAGE",
            "DOCKER_IMAGE_NAME",
        )
        host_os = (os.environ.get("PNQ_HOST_OS") or "").strip() or None
        host_ip = (os.environ.get("PNQ_HOST_IP") or "").strip() or None
        host_public_ip = _best_effort_public_ip()

        # Best-effort default gateway IP when host_ip isn't explicitly provided.
        # This is still not guaranteed to be the real host IP in all Docker network modes.
        if not host_ip:
            try:
                # linux-only
                with open("/proc/net/route", "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split()
                        # columns: Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT
                        if len(parts) < 3:
                            continue
                        destination = parts[1]
                        gateway = parts[2]
                        if destination == "00000000":
                            # gateway is hex little-endian
                            gw_hex = gateway
                            gw_int = int(gw_hex, 16)
                            gw_bytes = gw_int.to_bytes(4, byteorder="little", signed=False)
                            host_ip = socket.inet_ntoa(gw_bytes)
                            break
            except Exception:
                host_ip = None

        # Secondary fallback: Docker often sets resolv.conf nameserver to the gateway.
        if not host_ip:
            try:
                with open("/etc/resolv.conf", "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("nameserver"):
                            parts = line.split()
                            if len(parts) >= 2:
                                host_ip = parts[1]
                                break
            except Exception:
                host_ip = None

        payload = {
            "service_name": self.service_name,
            "server_name": self._server_name,
            "hostname": self._hostname,
            "ip_address": host_ip,
            "os": os_name,
            "os_distro": os_distro,
            "os_distro_version": os_distro_version,
            "arch": arch,
            "pid": pid,
            "app_version": app_version,
            "git_sha": git_sha,
            "container_name": container_name,
            "image": image,
            "host_os": host_os,
            "host_ip": host_ip,
            "host_public_ip": host_public_ip,
            "status": self._status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "started_at": self._started_at,
            "uptime_seconds": int(
                (datetime.now(timezone.utc) - datetime.fromisoformat(self._started_at)).total_seconds()
            ),
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "extra": self._extra,
        }
        dep = _read_deployment_env_for_payload()
        if dep:
            payload["deployment_env"] = dep
        if self._service_tag:
            payload["tag"] = self._service_tag
        return payload

    def _send_beat(self):
        """Send one heartbeat to Redis."""
        try:
            r = self._get_redis()
            key = self._build_key()
            payload = self._build_payload()
            r.setex(key, self.ttl_seconds, json.dumps(payload))
            logger.debug(f"Heartbeat sent: {key}")
        except Exception as e:
            logger.warning(f"Heartbeat send failed: {e}")
            # Reset redis connection so it reconnects next time
            self._redis = None

    def _loop(self):
        """Background heartbeat loop."""
        while not self._stop_event.is_set():
            self._send_beat()
            self._stop_event.wait(self.interval_seconds)

    def start(self):
        """Start the background heartbeat thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Heartbeat already running")
            return

        self._stop_event.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"heartbeat-{self.service_name}")
        self._thread.start()
        logger.info(f"Heartbeat started for {self.service_name} (every {self.interval_seconds}s)")

    def stop(self):
        """Stop the background heartbeat and send a final 'stopped' beat."""
        self._status = "stopped"
        self._send_beat()
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(f"Heartbeat stopped for {self.service_name}")

    def update(self, status: str = None, processed_count: int = None, error_count: int = None, extra: dict = None):
        """Update heartbeat metrics. Called from the main service code."""
        if status:
            self._status = status
        if processed_count is not None:
            self._processed_count = processed_count
        if error_count is not None:
            self._error_count = error_count
        if extra:
            self._extra.update(extra)

    def beat_once(self, status: str = "completed", processed_count: int = 0, error_count: int = 0,
                  duration_seconds: int = 0, extra: dict = None):
        """
        Send a single heartbeat for batch/cron jobs that run and exit.
        Use this instead of start()/stop() for non-long-running services.
        """
        self._status = status
        self._processed_count = processed_count
        self._error_count = error_count
        self._extra = extra or {}
        if duration_seconds:
            self._extra["duration_seconds"] = duration_seconds
        self._send_beat()
        logger.info(f"One-time heartbeat sent for {self.service_name}: {status}")


# Convenience function for quick setup
def create_heartbeat(service_name: str, **kwargs) -> Heartbeat:
    """Create and start a heartbeat with sensible defaults."""
    hb = Heartbeat(service_name=service_name, **kwargs)
    hb.start()
    return hb

