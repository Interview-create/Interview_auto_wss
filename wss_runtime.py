import asyncio
import csv
import json
import re
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp
import socketio
from loguru import logger

from slot_evaluate import (
    evaluate_spin_from_cells,
    get_board_key,
    get_grid,
    get_mode,
    infer_grid_from_cells,
)
from wss_helpers import (
    build_account_csv_writer,
    build_account_logger,
    build_payload,
    decode_fish_spawn_id,
    decode_protobuf_to_json,
    decode_trigger_payload_to_tokens,
    encode_bullet_hit_payload,
    encode_free_spin_token_as_protobuf,
    encode_spin_amount_payload_ss01,
    encode_spin_amount_payload_ss02,
    encode_spin_amount_payload_ss03,
    extract_trigger_payload,
)

_NUMERIC_STR_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _is_numeric_string(value: str) -> bool:
    """判斷字串是否為純數字格式。"""
    return bool(_NUMERIC_STR_RE.fullmatch(value.strip()))


_MISSING_SYMBOL_LOCK = threading.Lock()


def _log_missing_symbol_to_csv(
    run_dir: Path, pid: str, missing_key: Any, cells: Any
) -> None:
    """當遇到 symbol_map 缺少對應 key 時，將紀錄寫入獨立的 CSV。"""
    csv_path = run_dir / "missing_symbols.csv"
    with _MISSING_SYMBOL_LOCK:
        write_header = not csv_path.exists()
        with csv_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["time", "pid", "missing_key", "cells"])
            writer.writerow(
                [
                    datetime.now().isoformat(),
                    pid,
                    str(missing_key),
                    json.dumps(cells, ensure_ascii=False),
                ]
            )


_SPIN_PAYLOAD_ENCODERS: dict[str, Callable[..., str]] = {
    "ss01": encode_spin_amount_payload_ss01,
    "ss02": encode_spin_amount_payload_ss02,
    "ss03": encode_spin_amount_payload_ss03,
}


def _resolve_profile_value(field_cfg: Any, loop_cfg: dict[str, Any]) -> Any:
    """依 profile 定義的 source / fallback / default 規則，從 events.loop 取值。"""
    if not isinstance(field_cfg, dict):
        return field_cfg

    source_key = field_cfg.get("source")
    if isinstance(source_key, str) and source_key in loop_cfg:
        return loop_cfg[source_key]

    fallback_key = field_cfg.get("fallback")
    if isinstance(fallback_key, str) and fallback_key in loop_cfg:
        return loop_cfg[fallback_key]

    return field_cfg.get("default")


def _build_slot_loop_payload(
    cfg: dict[str, Any],
    loop_cfg: dict[str, Any],
    account_logger: Any,
) -> Any:
    """依 profile 內的 payload 設定，組出 slot `client:spin` 要送的資料。"""
    profile_encoder = cfg.get("spin_payload_encoder")
    if not isinstance(profile_encoder, str) or not profile_encoder:
        return loop_cfg.get("bet_amount", loop_cfg.get("data"))

    encoder = _SPIN_PAYLOAD_ENCODERS.get(profile_encoder)
    if encoder is None:
        account_logger.warning(
            "unknown spin payload encoder={}, using raw loop data",
            profile_encoder,
        )
        return loop_cfg.get("bet_amount", loop_cfg.get("data"))

    field_map = cfg.get("spin_payload_fields", {})
    if not isinstance(field_map, dict):
        account_logger.warning(
            "invalid spin_payload_fields for pid={}, using raw loop data",
            cfg.get("pid", ""),
        )
        return loop_cfg.get("bet_amount", loop_cfg.get("data"))

    resolved_kwargs = {
        field_name: _resolve_profile_value(field_cfg, loop_cfg)
        for field_name, field_cfg in field_map.items()
    }
    return encoder(**resolved_kwargs)


async def call_with_log(
    sio: socketio.AsyncClient,
    account_logger: Any,
    csv_writer: Any,
    event_name: str,
    data: Any,
    tag: str,
    slot_evaluate_enabled: bool,
    pid: str,
    bet_amount: float,
    loop_state: dict[str, Any],
    run_dir: Path,
    wait_timeout: int = 10,
) -> None:
    """發送 socket 事件並等待 ACK，同時記錄日誌。"""
    if loop_state.get("start_time") is None:
        loop_state["start_time"] = datetime.now().isoformat()

    account_logger.info("CALL -> event={}, tag={}, data={}", event_name, tag, data)

    if isinstance(data, str):
        decoded = decode_protobuf_to_json(data, pid=pid, event_name=f"{event_name}:req")
        if decoded:
            account_logger.info("CALL DECODED -> \n{}", decoded)

    try:
        ack_args = await sio.call(event_name, data, timeout=wait_timeout)
        loop_state["end_time"] = datetime.now().isoformat()
        account_logger.info(
            "ACK <- event={}, tag={}, ack={}",
            event_name,
            tag,
            str(ack_args),
        )

        if not isinstance(ack_args, tuple):
            ack_args = (ack_args,)

        for arg in ack_args:
            if isinstance(arg, str):
                response = decode_protobuf_to_json(arg, pid=pid, event_name=event_name)
                account_logger.info("ON DECODED -> \n{}", response)

                if response and event_name in ("client:spin", "client:free_spin"):
                    try:
                        res_data = json.loads(response)
                        payout = res_data.get("total_payout")
                        payout_val = 0.0
                        if isinstance(payout, dict):
                            payout_val = float(
                                payout.get("value", payout.get("1", 0.0))
                            )
                        elif isinstance(payout, (int, float, str)):
                            payout_val = float(payout)

                        if event_name == "client:free_spin":
                            loop_state["total_free_spin_win_amount"] = round(
                                loop_state["total_free_spin_win_amount"] + payout_val, 4
                            )
                        else:
                            loop_state["total_spin_win_amount"] = round(
                                loop_state["total_spin_win_amount"] + payout_val, 4
                            )
                    except Exception:
                        pass

                if slot_evaluate_enabled and event_name in (
                    "client:spin",
                    "client:free_spin",
                ):
                    _maybe_evaluate_slot_spin(
                        account_logger,
                        response,
                        pid,
                        bet_amount,
                        run_dir,
                    )
                if response and csv_writer:
                    try:
                        csv_writer(event_name, tag, response)
                    except Exception:
                        pass

    except Exception:
        account_logger.exception("CALL failed -> event={}, tag={}", event_name, tag)
        raise


def create_socket_client(
    account_logger: Any,
    csv_writer: Any,
    pending_free_spin_tokens: deque[str],
    game_type: str,
    runtime_state: dict[str, Optional[int]],
    pid: str,
) -> socketio.AsyncClient:
    """建立 socket client 並註冊事件處理。"""
    sio = socketio.AsyncClient(
        reconnection=False,
        logger=False,
        engineio_logger=False,
        request_timeout=30,
    )

    @sio.event
    async def connect() -> None:
        account_logger.info("connected")

    @sio.event
    async def disconnect() -> None:
        account_logger.info("disconnected")

    @sio.on("*")  # type: ignore
    async def catch_all(event: str, *args: Any) -> None:
        account_logger.info("ON <- event={}, data={}", event, str(args))

        payload_b64_log = extract_trigger_payload(args)
        if payload_b64_log:
            decoded = decode_protobuf_to_json(
                payload_b64_log, pid=pid, event_name=event
            )
            if decoded:
                account_logger.info("ON DECODED -> \n{}", decoded)

        if game_type == "slot":
            if event != "server:free_spin:triggered":
                return

            payload_b64 = extract_trigger_payload(args)
            if not payload_b64:
                account_logger.warning("free_spin triggered but payload missing")
                return

            try:
                tokens = decode_trigger_payload_to_tokens(payload_b64)
            except Exception as decode_error:
                account_logger.warning(
                    "free_spin payload decode failed: {}",
                    decode_error,
                )
                return

            if not tokens:
                account_logger.info("free_spin triggered but no valid tokens")
                return

            pending_free_spin_tokens.extend(tokens)
            account_logger.info(
                "free_spin triggered, queued {} token(s)",
                len(tokens),
            )

        if game_type == "fish":
            if event != "server:fish:spawn":
                return

            payload_b64 = extract_trigger_payload(args)
            if not payload_b64:
                account_logger.warning("fish spawn event missing payload")
                return

            decoded = decode_protobuf_to_json(payload_b64, pid=pid, event_name=event)
            if decoded and csv_writer:
                try:
                    csv_writer(event, "fish_spawn", decoded)
                except Exception:
                    pass

            try:
                fish_id = decode_fish_spawn_id(payload_b64)
            except Exception as decode_error:
                account_logger.warning("fish spawn decode failed: {}", decode_error)
                return
            runtime_state["latest_fish_id"] = fish_id
            account_logger.info("fish spawn updated latest fish id={}", fish_id)

    return sio


def _maybe_evaluate_slot_spin(
    account_logger: Any,
    decoded_json: Optional[str],
    pid: str,
    bet_amount: float,
    run_dir: Path,
) -> None:
    """若 payload 具備盤面資料，執行評分。"""
    if not decoded_json:
        return
    try:
        response = json.loads(decoded_json)
    except Exception:
        return
    board_key = get_board_key(pid)
    path = board_key if isinstance(board_key, (list, tuple)) else [board_key]
    for key in path:
        if isinstance(response, list) and len(response) > 0:
            response = response[0]
        if not isinstance(response, dict):
            return
        response = response.get(key)
    cells = response
    if not isinstance(cells, list):
        return

    mode = get_mode(pid)
    if mode == "payline":
        slot_rows, slot_cols = get_grid(pid)
    else:
        slot_rows, slot_cols = infer_grid_from_cells(cells)

    try:
        result = evaluate_spin_from_cells(
            cells,
            slot_rows,
            slot_cols,
            pid,
            mode,
            bet_amount,
        )
    except KeyError as e:
        missing_key = e.args[0] if e.args else "Unknown"
        account_logger.error(
            "evaluate_spin_from_cells KeyError, missing symbol_map key: {}", missing_key
        )
        _log_missing_symbol_to_csv(run_dir, pid, missing_key, cells)
        return
    except Exception as e:
        account_logger.error("evaluate_spin_from_cells Exception: {}", e)
        return

    line_wins = result.get("line_wins", [])
    win_details = [f"line_{w.get('line')}: win_{w.get('payout')}" for w in line_wins]
    cascading_wins = result.get("cascading_wins", [])
    cascading_details = [
        "symbol_{}: count_{} positions_{} payout_{}".format(
            w.get("symbol"),
            w.get("count", w.get("match")),
            w.get("positions"),
            w.get("payout"),
        )
        for w in cascading_wins
    ]

    account_logger.info(
        "SLOT EVAL -> total_win={}, line_wins={}, details={}, cascading_wins={}, cascading_details={}, scatter={}",
        result.get("total_win"),
        len(line_wins),
        win_details,
        len(cascading_wins),
        cascading_details,
        result.get("scatter"),
    )


async def run_single_attempt(
    loginname: str,
    cfg: dict[str, Any],
    socket_cfg: dict[str, Any],
    account_logger: Any,
    csv_writer: Any,
    loop_state: dict[str, Any],
    slot_evaluate_enabled: bool,
    run_dir: Path,
) -> None:
    """單次連線流程：verify 取 token、connect socket、送 init、進主迴圈。"""
    pending_free_spin_tokens: deque[str] = deque()
    runtime_state: dict[str, Optional[int]] = {"latest_fish_id": None}
    wait_timeout = socket_cfg.get("wait_timeout", 10)

    # 第一步：先走 verify API，拿到這次 WebSocket 連線要用的 access token。
    async with aiohttp.ClientSession() as session:
        payload = build_payload(cfg["payload_template"], loginname)
        account_logger.info(
            "verify request: method=POST url={} payload={}", cfg["verify_url"], payload
        )
        async with session.post(
            cfg["verify_url"],
            headers=cfg["headers"],
            json=payload,
        ) as resp:
            response_data = await resp.json(content_type=None)
            account_logger.info(
                "verify response: status={} body={}", resp.status, response_data
            )

    try:
        access_token = response_data["data"]["accessToken"]
    except (KeyError, TypeError) as e:
        raise ValueError(
            f"verify API response missing accessToken (loginname={loginname}): {e}"
        ) from e
    loop_cfg = cfg["events"]["loop"]
    game_type = cfg.get("gameType", "")
    pid = cfg.get("pid", "")

    # 第二步：建立 socket client，並把遊戲特定的 server event handler 綁上去。
    sio = create_socket_client(
        account_logger,
        csv_writer,
        pending_free_spin_tokens,
        game_type,
        runtime_state,
        pid,
    )

    # 第三步：真正連進 socket server。
    socket_url = (
        f"{cfg['socket_base_url']}?access_token={access_token}&platform={socket_cfg['platform_query']}"
        + (f"&game_id={cfg['socket_game_id']}" if cfg.get("socket_game_id") else "")
    )
    account_logger.info("socket connect: url={}", socket_url)
    await sio.connect(
        socket_url,
        transports=["websocket"],
        socketio_path=cfg["socket_path"],
        wait_timeout=socket_cfg["wait_timeout"],
    )

    # 第四步：先送 init events，這些通常是進房或快照類事件。
    for idx, event_cfg in enumerate(cfg["events"]["init"], start=1):
        await call_with_log(
            sio=sio,
            account_logger=account_logger,
            csv_writer=csv_writer,
            event_name=event_cfg["name"],
            data=event_cfg["data"],
            tag=f"init#{idx}",
            slot_evaluate_enabled=slot_evaluate_enabled,
            pid=pid,
            bet_amount=1.0,
            loop_state=loop_state,
            run_dir=run_dir,
            wait_timeout=wait_timeout,
        )

    run_all_cfg = cfg["run_all"]
    spin_round = run_all_cfg["spin_round"]
    loop_data: Any = None
    bet_amount_value = 1.0
    raw_bet_amount = loop_cfg.get("bet_amount", loop_cfg.get("data"))
    if isinstance(raw_bet_amount, (int, float)):
        bet_amount_value = float(raw_bet_amount)
    elif isinstance(raw_bet_amount, str) and _is_numeric_string(raw_bet_amount):
        bet_amount_value = float(raw_bet_amount.strip())

    # 第五步：slot 遊戲的 spin payload 交給 profile 驅動的 encoder 組裝。
    # 這樣新增 slot 機台時，只要補 profile 設定，不必再擴增 if/elif 分支。
    if game_type == "slot":
        loop_data = _build_slot_loop_payload(cfg, loop_cfg, account_logger)

    # 第六步：進入主迴圈，重複送 spin / bullet hit，直到達到 spin_round。
    while spin_round is None or loop_state["count"] < spin_round:
        if not sio.connected:
            raise ConnectionError("socket disconnected unexpectedly")

        if game_type == "slot":
            if pending_free_spin_tokens:
                # 若 server 先前觸發 free spin，優先把 queue 裡的 token 消耗掉。
                token = pending_free_spin_tokens.popleft()
                try:
                    encoded_token = encode_free_spin_token_as_protobuf(token)
                except Exception as encode_error:
                    account_logger.error(
                        "protobuf encode failed for token {}: {}",
                        token,
                        encode_error,
                    )
                    continue
                loop_state["free_spin_emit_count"] += 1
                try:
                    await call_with_log(
                        sio=sio,
                        account_logger=account_logger,
                        csv_writer=csv_writer,
                        event_name="client:free_spin",
                        data=encoded_token,
                        tag=f"free_spin#{loop_state['free_spin_emit_count']}",
                        slot_evaluate_enabled=slot_evaluate_enabled,
                        pid=pid,
                        bet_amount=bet_amount_value,
                        loop_state=loop_state,
                        run_dir=run_dir,
                        wait_timeout=wait_timeout,
                    )
                except Exception as emit_error:
                    account_logger.error(
                        "free_spin emit failed for token {}: {}",
                        token,
                        emit_error,
                    )
                else:
                    account_logger.info("free_spin sent token {}", token)

                await asyncio.sleep(run_all_cfg["interval_seconds"])
                continue
        else:
            if game_type == "fish":
                # fish 遊戲需要先等 server 告訴我們最新 fish_id，才能送 hit。
                latest_fish_id = runtime_state["latest_fish_id"]
                if latest_fish_id is None:
                    account_logger.info("waiting for server:fish:spawn before hit emit")
                    await asyncio.sleep(run_all_cfg["interval_seconds"])
                    continue
                bullet_hit_type = loop_cfg.get("bullet_hit_type", 1)
                try:
                    loop_data = encode_bullet_hit_payload(
                        latest_fish_id, bullet_hit_type
                    )
                except Exception as encode_error:
                    account_logger.error(
                        "bullet hit payload encode failed: fish_id={}, bullet_hit_type={}, error={}",
                        latest_fish_id,
                        bullet_hit_type,
                        encode_error,
                    )
                    await asyncio.sleep(run_all_cfg["interval_seconds"])
                    continue

        loop_state["count"] += 1
        await call_with_log(
            sio=sio,
            account_logger=account_logger,
            csv_writer=csv_writer,
            event_name=loop_cfg["name"],
            data=loop_data,
            tag=f"spin#{loop_state['count']}",
            slot_evaluate_enabled=slot_evaluate_enabled,
            pid=pid,
            bet_amount=bet_amount_value,
            loop_state=loop_state,
            run_dir=run_dir,
            wait_timeout=wait_timeout,
        )

        await asyncio.sleep(run_all_cfg["interval_seconds"])

        summary_batch_size = run_all_cfg.get("summary_batch_size", 10000)
        if game_type != "fish" and csv_writer and summary_batch_size > 0:
            if loop_state["count"] % summary_batch_size == 0:
                try:
                    csv_writer(
                        "summary",
                        "total",
                        {
                            "start_time": loop_state.get("start_time"),
                            "end_time": loop_state.get("end_time"),
                            "spin_count": loop_state["count"],
                            "free_spin_count": loop_state["free_spin_emit_count"],
                            "total_spin_win": loop_state["total_spin_win_amount"],
                            "total_free_spin_win": loop_state[
                                "total_free_spin_win_amount"
                            ],
                            "bet_amount": bet_amount_value,
                        },
                    )
                except Exception as e:
                    account_logger.warning(
                        "Failed to write intermediate summary CSV: {}", e
                    )

    # 第七步：主迴圈結束後安全斷線。
    await sio.disconnect()

    # 第八步：把這個帳號本次執行的總結寫進 summary CSV。
    if game_type != "fish" and csv_writer:
        summary_batch_size = run_all_cfg.get("summary_batch_size", 10000)
        if (
            summary_batch_size <= 0
            or loop_state["count"] % summary_batch_size != 0
            or loop_state["count"] == 0
        ):
            try:
                csv_writer(
                    "summary",
                    "total",
                    {
                        "start_time": loop_state.get("start_time"),
                        "end_time": loop_state.get("end_time"),
                        "spin_count": loop_state["count"],
                        "free_spin_count": loop_state["free_spin_emit_count"],
                        "total_spin_win": loop_state["total_spin_win_amount"],
                        "total_free_spin_win": loop_state["total_free_spin_win_amount"],
                        "bet_amount": bet_amount_value,
                    },
                )
            except Exception as e:
                account_logger.warning("Failed to write summary CSV: {}", e)


async def run_account_main(
    loginname: str,
    cfg: dict[str, Any],
    retry_cfg: dict[str, Any],
    socket_cfg: dict[str, Any],
    run_dir: Path,
    log_level: str,
    csv_enabled: bool = False,
    slot_evaluate_enabled: bool = False,
) -> None:
    """單帳號主流程，含重試邏輯。"""
    rotation = cfg.get("logging", {}).get("rotation", "100 MB")
    account_logger, sink_id = build_account_logger(
        run_dir, loginname, log_level, rotation
    )

    csv_writer_cfg = cfg.get("csv_writer", {})
    if isinstance(csv_writer_cfg, dict):
        csv_rotation = csv_writer_cfg.get("rotation", "100 MB")
    else:
        csv_rotation = "100 MB"

    csv_writer = (
        build_account_csv_writer(
            run_dir, loginname, log_level, cfg["pid"], csv_rotation
        )
        if csv_enabled
        else None
    )
    loop_state: dict[str, Any] = {
        "count": 0,
        "free_spin_emit_count": 0,
        "total_spin_win_amount": 0.0,
        "total_free_spin_win_amount": 0.0,
        "start_time": None,
        "end_time": None,
    }
    max_retries = retry_cfg["max_retries"]
    base_delay = retry_cfg["base_delay"]
    max_delay = retry_cfg["max_delay"]

    try:
        for attempt in range(max_retries):
            try:
                await run_single_attempt(
                    loginname,
                    cfg,
                    socket_cfg,
                    account_logger,
                    csv_writer,
                    loop_state,
                    slot_evaluate_enabled,
                    run_dir,
                )
                break  # 執行成功後跳出重試迴圈
            except Exception as e:
                account_logger.error(
                    "attempt {}/{} failed: {}",
                    attempt + 1,
                    max_retries,
                    e,
                )
                account_logger.exception("exception traceback")
                if attempt < max_retries - 1:
                    delay = min(base_delay * (2**attempt), max_delay)
                    account_logger.warning("retry after {}s", delay)
                    await asyncio.sleep(delay)
                else:
                    account_logger.error("reached max retries")
                    return
    finally:
        logger.remove(sink_id)
