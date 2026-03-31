import asyncio
import csv
import re
from pathlib import Path
from typing import Any

from loguru import logger

from wss_helpers import build_run_dir, load_env
from wss_runtime import run_account_main

ENV_PATH = Path(__file__).with_name("env.json")


async def main(
    loginname: str,
    cfg: dict[str, Any],
    retry_cfg: dict[str, Any],
    socket_cfg: dict[str, Any],
    run_dir: Path,
    log_level: str,
    csv_enabled: bool,
    slot_evaluate_enabled: bool,
) -> None:
    await run_account_main(
        loginname,
        cfg,
        retry_cfg,
        socket_cfg,
        run_dir,
        log_level,
        csv_enabled,
        slot_evaluate_enabled,
    )


async def run_all(
    cfg: dict[str, Any],
    retry_cfg: dict[str, Any],
    socket_cfg: dict[str, Any],
    run_dir: Path,
    log_level: str,
    csv_enabled: bool,
    slot_evaluate_enabled: bool,
) -> None:
    run_cfg = cfg["run_all"]
    semaphore = asyncio.Semaphore(run_cfg["concurrency"])

    async def limited_worker(loginname: str) -> None:
        async with semaphore:
            await main(
                loginname,
                cfg,
                retry_cfg,
                socket_cfg,
                run_dir,
                log_level,
                csv_enabled,
                slot_evaluate_enabled,
            )

    tasks = []
    stagger_seconds = run_cfg["stagger_seconds"]
    account_template_list = run_cfg.get("account_template_list", [])

    loginnames: list[str]
    if isinstance(account_template_list, list) and len(account_template_list) > 0:
        loginnames = [str(item) for item in account_template_list]
    else:
        start = run_cfg["start"]
        end = run_cfg["end"] + 1
        account_template = run_cfg["account_template"]
        loginnames = [account_template.format(i=i) for i in range(start, end)]

    for loginname in loginnames:
        tasks.append(asyncio.create_task(limited_worker(loginname)))
        await asyncio.sleep(stagger_seconds)

    await asyncio.gather(*tasks)

    if not csv_enabled:
        return

    total_summary_path = run_dir / "total_summary.csv"
    if not total_summary_path.exists():
        return

    def sort_key(row: dict[str, str]) -> tuple[int, Any]:
        """帳號尾碼有數字時用數字排序，否則退回字串排序。"""
        match = re.search(r"(\d+)$", row["account"])
        if match:
            return (0, int(match.group(1)))
        return (1, row["account"])

    fieldnames: list[str] = []
    sorted_rows: list[dict[str, str]] = []
    with total_summary_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []  # type: ignore
        sorted_rows = sorted(list(reader), key=sort_key)

    with total_summary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted_rows)


if __name__ == "__main__":
    (
        config,
        logging_config,
        retry_config,
        socket_config,
        csv_enabled,
        slot_evaluate_enabled,
    ) = load_env(ENV_PATH)
    logger.remove()
    run_log_dir = build_run_dir(logging_config, config["pid"])
    level = str(logging_config["level"]).upper()
    asyncio.run(
        run_all(
            config,
            retry_config,
            socket_config,
            run_log_dir,
            level,
            csv_enabled,
            slot_evaluate_enabled,
        )
    )
