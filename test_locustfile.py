import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Optional

from locust import User, between, task
from loguru import logger

from wss_helpers import build_run_dir, load_env
from wss_runtime import run_account_main

ENV_PATH = Path(__file__).with_name("env.json")

CONFIG, LOGGING_CONFIG, RETRY_CONFIG, SOCKET_CONFIG, *_ = load_env(ENV_PATH)
RUN_CFG = CONFIG["run_all"]
LOCUST_CFG: dict[str, Any] = CONFIG.get("locust", {})

_ACCOUNT_TEMPLATE_LIST = RUN_CFG.get("account_template_list", [])
if isinstance(_ACCOUNT_TEMPLATE_LIST, list) and len(_ACCOUNT_TEMPLATE_LIST) > 0:
    _LOGIN_NAMES = [str(item) for item in _ACCOUNT_TEMPLATE_LIST]
else:
    _START = int(RUN_CFG["account_start"])
    _END = int(RUN_CFG["account_end"])
    _TEMPLATE = str(RUN_CFG["account_template"])
    _LOGIN_NAMES = [_TEMPLATE.format(i=i) for i in range(_START, _END + 1)]

if not _LOGIN_NAMES:
    raise ValueError("run_all produced no login names")

_WAIT_MIN = float(LOCUST_CFG.get("wait_min_seconds", 0.0))
_WAIT_MAX = float(LOCUST_CFG.get("wait_max_seconds", _WAIT_MIN))
if _WAIT_MAX < _WAIT_MIN:
    _WAIT_MAX = _WAIT_MIN

_LOGIN_COUNTER = 0
_LOGIN_LOCK = threading.Lock()


def next_loginname() -> str:
    global _LOGIN_COUNTER
    with _LOGIN_LOCK:
        index = _LOGIN_COUNTER
        _LOGIN_COUNTER = (_LOGIN_COUNTER + 1) % len(_LOGIN_NAMES)
    return _LOGIN_NAMES[index]


class WssToolUser(User):
    wait_time = between(_WAIT_MIN, _WAIT_MAX)

    def on_start(self) -> None:
        self.loginname = next_loginname()
        self.log_level = str(LOGGING_CONFIG.get("level", "INFO")).upper()
        self.run_dir = build_run_dir(LOGGING_CONFIG, CONFIG["pid"])

    @task
    def run_wss_flow(self) -> None:
        start_time = time.perf_counter()
        exc: Optional[Exception] = None

        try:
            asyncio.run(
                run_account_main(
                    loginname=self.loginname,
                    cfg=CONFIG,
                    retry_cfg=RETRY_CONFIG,
                    socket_cfg=SOCKET_CONFIG,
                    run_dir=self.run_dir,
                    log_level=self.log_level,
                )
            )
        except Exception as e:
            exc = e
            logger.exception("locust user flow failed for {}", self.loginname)

        duration_ms = (time.perf_counter() - start_time) * 1000
        self.environment.events.request.fire(
            request_type="WSS",
            name="run_account_main",
            response_time=duration_ms,
            response_length=0,
            exception=exc,
        )
