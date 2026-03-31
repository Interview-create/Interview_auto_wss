import base64
import csv
import json
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import blackboxprotobuf
from loguru import logger

from slot_profiles import _PROTO_SCHEMAS, SLOT_PROFILES

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _decode_varint(data: bytes, start: int) -> tuple[int, int]:
    """解碼 protobuf varint，回傳值與新索引。"""
    shift = 0
    value = 0
    index = start
    while index < len(data):
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, index
        shift += 7
        if shift > 63:
            break
    raise ValueError("invalid protobuf varint")


def _encode_varint(value: int) -> bytes:
    """將非負整數編碼為 protobuf varint。"""
    if value < 0:
        raise ValueError("varint value must be non-negative")
    out = bytearray()
    current = value
    while current >= 0x80:
        out.append((current & 0x7F) | 0x80)
        current >>= 7
    out.append(current)
    return bytes(out)


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """遞迴合併 dict，讓 env.json 的本次執行設定覆蓋 game profile 預設值。"""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def load_env(
    path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    bool,
    bool,
]:
    """讀取 env.json，並和 slot_profiles 內的固定遊戲設定合併成 runtime config。"""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "PID" not in data:
        raise ValueError("env.json missing 'PID'")
    pid = data["PID"]
    if pid not in data:
        raise ValueError(f"env.json PID '{pid}' not found")
    if "logging" not in data:
        raise ValueError("env.json missing top-level 'logging'")
    if "retry" not in data:
        raise ValueError("env.json missing top-level 'retry'")
    if "socket" not in data:
        raise ValueError("env.json missing top-level 'socket'")
    if "run_all" not in data:
        raise ValueError("env.json missing top-level 'run_all'")
    if pid not in SLOT_PROFILES:
        raise ValueError(f"game profile '{pid}' not found in game_profiles.yaml")
    logging_cfg = data["logging"]
    retry_cfg = data["retry"]
    socket_cfg = data["socket"]
    run_all_cfg = data["run_all"]
    if not all(k in logging_cfg for k in ("enabled", "dir", "level")):
        raise ValueError()
    if "platform_query" not in socket_cfg or "wait_timeout" not in socket_cfg:
        raise ValueError(
            "env.json top-level 'socket' missing 'platform_query' or 'wait_timeout'"
        )
    if not isinstance(run_all_cfg, dict):
        raise ValueError("env.json top-level 'run_all' must be an object")
    # `env.json` 只放這次執行想改的內容，例如 bet、events、run_all。
    env_active_cfg = data.get(pid, {})
    if env_active_cfg and not isinstance(env_active_cfg, dict):
        raise ValueError(f"env.json PID '{pid}' must map to an object")

    # 先以 game_profiles.yaml 為基底，再套上 env.json 的局部覆寫。
    active_cfg = _deep_merge_dict(
        SLOT_PROFILES[pid], env_active_cfg if isinstance(env_active_cfg, dict) else {}
    )

    # `run_all` 是批次執行層的共用控制參數，獨立掛在 active config 上。
    active_cfg["run_all"] = dict(run_all_cfg)
    active_cfg["logging"] = dict(logging_cfg)
    active_cfg.setdefault("pid", pid)
    active_cfg.setdefault("gametype", active_cfg.get("gameType", ""))

    csv_writer_cfg = data.get("csv_writer", {})
    if isinstance(csv_writer_cfg, dict):
        csv_enabled = csv_writer_cfg.get("enabled", 0) == 1
    else:
        csv_enabled = csv_writer_cfg == 1
    active_cfg["csv_writer"] = csv_writer_cfg

    slot_evaluate_enabled = data.get("slot_evaluate") == 1
    return (
        active_cfg,
        logging_cfg,
        retry_cfg,
        socket_cfg,
        csv_enabled,
        slot_evaluate_enabled,
    )


def build_run_dir(logging_cfg: dict[str, Any], pid: str) -> Path:
    """依時間建立執行目錄。"""
    now = datetime.now()
    base_dir = Path(logging_cfg["dir"])
    run_dir = base_dir / now.strftime("%Y%m%d") / f"{pid}_{now.strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def build_account_logger(
    run_dir: Path, loginname: str, level: str, rotation: str = "100 MB"
) -> Tuple[Any, int]:
    """為單一帳號建立 loguru logger 與 sink。"""
    account_logger = logger.bind(account=loginname)
    sink_id = account_logger.add(
        str(run_dir / f"{loginname}.log"),
        level=level,
        rotation=rotation,
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSSSS} | {level} | {extra[account]} | {message}",
        enqueue=True,
        filter=lambda record, name=loginname: record["extra"].get("account") == name,
    )
    return account_logger, sink_id


def _parse_rotation_size(size_str: str) -> int:
    """將類似 '100 MB', '1 GB' 的字串轉換為 bytes 供手動分割使用。"""
    if not isinstance(size_str, str):
        return 100 * 1024 * 1024
    s = size_str.strip().upper()
    multiplier = 1
    if s.endswith("KB"):
        multiplier = 1024
        s = s[:-2]
    elif s.endswith("MB"):
        multiplier = 1024 * 1024
        s = s[:-2]
    elif s.endswith("GB"):
        multiplier = 1024 * 1024 * 1024
        s = s[:-2]
    try:
        return int(float(s.strip()) * multiplier)
    except ValueError:
        return 100 * 1024 * 1024


def build_account_csv_writer(
    run_dir: Path, loginname: str, log_level: str, pid: str, rotation: str = "100 MB"
) -> Callable[[str, str, Any], None]:
    """建立 CSV 寫入 callable，讓 runtime 可以像呼叫 logger 一樣寫 CSV。"""
    max_bytes = _parse_rotation_size(rotation)

    def _write(event: str, tag: str, data: Any) -> None:
        write_csv_log(run_dir, loginname, event, tag, data, pid, max_bytes)

    _ = log_level
    return _write


def build_payload(payload_template: dict[str, Any], loginname: str) -> dict[str, Any]:
    """把 loginname 套進 verify payload template，組出登入驗證用 JSON。"""
    payload: dict[str, Any] = {}
    for key, value in payload_template.items():
        if isinstance(value, str):
            payload[key] = value.replace("{loginname}", loginname)
        else:
            payload[key] = value
    return payload


def decode_trigger_payload_to_tokens(payload_b64: str) -> list[str]:
    """解析 free spin 觸發 payload，抽出後續 client:free_spin 需要的 token。"""
    padding = (-len(payload_b64)) % 4
    padded = payload_b64 + ("=" * padding)
    decoded_bytes = base64.b64decode(padded)
    token_bytes = re.findall(rb"\$[0-9a-fA-F-]{36}", decoded_bytes)
    return [token.decode("ascii") for token in token_bytes]


def encode_free_spin_token_as_protobuf(token: str) -> str:
    """將 free spin token 以 protobuf 格式封裝後再 base64。"""
    normalized = token[1:] if token.startswith("$") else token
    if not _UUID_RE.fullmatch(normalized):
        raise ValueError(f"invalid free spin token uuid: {token}")

    value = normalized.encode("utf-8")
    if len(value) > 127:
        raise ValueError(f"free spin token too long: {len(value)}")

    # field 1, wire type 2 (length-delimited)
    protobuf_bytes = bytes([0x0A, len(value)]) + value
    return base64.b64encode(protobuf_bytes).decode("utf-8")


def encode_spin_amount_payload_ss01(bet_amount: str) -> str:
    """SS01 的 spin request 很單純，只有 bet_amount 一個欄位。"""
    value = str(bet_amount).encode("utf-8")
    length_bytes = _encode_varint(len(value))
    protobuf_bytes = b"\x0a" + length_bytes + value
    return base64.b64encode(protobuf_bytes).decode("utf-8")


def encode_spin_amount_payload_ss02(
    bet_amount: str,
    currency_code: int,
    display_scale: int,
    compact_notation: int,
) -> str:
    """將 SS02 的下注資料封裝成巢狀 protobuf，再轉成 base64。"""
    bet_amount_bytes = str(bet_amount).encode("utf-8")
    currency_code_bytes = str(currency_code).encode("utf-8")
    display_scale_int = _normalize_non_negative_int(display_scale, "display_scale")
    compact_notation_int = _normalize_non_negative_int(
        compact_notation, "compact_notation"
    )

    inner = (
        b"\x0a"
        + _encode_varint(len(bet_amount_bytes))
        + bet_amount_bytes
        + b"\x12"
        + _encode_varint(len(currency_code_bytes))
        + currency_code_bytes
        + b"\x18"
        + _encode_varint(display_scale_int)
        + b"\x20"
        + _encode_varint(compact_notation_int)
    )
    outer = b"\x0a" + _encode_varint(len(inner)) + inner
    return base64.b64encode(outer).decode("utf-8")


def encode_spin_amount_payload_ss03(
    bet_amount: str,
    currency_code: int,
    display_scale: int,
    compact_notation: int,
) -> str:
    """將 SS03 的下注資料封裝成巢狀 protobuf，再轉成 base64。"""
    bet_amount_bytes = str(bet_amount).encode("utf-8")
    currency_code_bytes = str(currency_code).encode("utf-8")
    display_scale_int = _normalize_non_negative_int(display_scale, "display_scale")
    compact_notation_int = _normalize_non_negative_int(
        compact_notation, "compact_notation"
    )

    inner = (
        b"\x0a"
        + _encode_varint(len(bet_amount_bytes))
        + bet_amount_bytes
        + b"\x12"
        + _encode_varint(len(currency_code_bytes))
        + currency_code_bytes
        + b"\x18"
        + _encode_varint(display_scale_int)
        + b"\x20"
        + _encode_varint(compact_notation_int)
    )
    outer = b"\x0a" + _encode_varint(len(inner)) + inner
    return base64.b64encode(outer).decode("utf-8")


def decode_fish_spawn_id(payload_b64: str) -> int:
    """解出魚 spawn payload 中的 fish_id。"""
    padding = (-len(payload_b64)) % 4
    padded = payload_b64 + ("=" * padding)
    decoded = base64.b64decode(padded)

    index = 0
    while index < len(decoded):
        key, index = _decode_varint(decoded, index)
        field_number = key >> 3
        wire_type = key & 0x07

        if wire_type == 0:
            value, index = _decode_varint(decoded, index)
            if field_number == 1:
                return value
        elif wire_type == 1:
            index += 8
        elif wire_type == 2:
            length, index = _decode_varint(decoded, index)
            index += length
        elif wire_type == 5:
            index += 4
        else:
            raise ValueError("unsupported protobuf wire type: {}".format(wire_type))

        if index > len(decoded):
            raise ValueError("invalid protobuf payload length")

    raise ValueError("fish spawn payload missing field #1 fish id")


def _normalize_non_negative_int(value: Any, field_name: str) -> int:
    """驗證並正規化為非負整數。"""
    if isinstance(value, bool):
        raise ValueError(
            "invalid {}: must be a non-negative integer number".format(field_name)
        )

    if isinstance(value, int):
        normalized = value
    elif isinstance(value, float) and value.is_integer():
        normalized = int(value)
    else:
        raise ValueError(
            "invalid {}: must be a non-negative integer number".format(field_name)
        )

    if normalized < 0:
        raise ValueError(
            "invalid {}: must be a non-negative integer number".format(field_name)
        )
    return normalized


def encode_bullet_hit_payload(fish_id: int, bullet_hit_type: Any = 1) -> str:
    """組出魚機子彈命中 payload（base64）。"""
    normalized_fish_id = _normalize_non_negative_int(fish_id, "fish_id")
    normalized_hit_type = _normalize_non_negative_int(
        bullet_hit_type,
        "bullet_hit_type",
    )

    payload = (
        b"\x08"
        + _encode_varint(normalized_fish_id)
        + b"\x10"
        + _encode_varint(normalized_hit_type)
    )
    return base64.b64encode(payload).decode("utf-8")


def extract_trigger_payload(args: tuple[Any, ...]) -> Optional[str]:
    """從 socketio 回呼參數中找出第一個字串 payload，供 decode/protobuf 使用。"""
    queue: list[Any] = list(args)
    while queue:
        item = queue.pop(0)
        if isinstance(item, str) and item:
            return item
        if isinstance(item, (list, tuple)):
            queue.extend(item)
        elif isinstance(item, dict):
            queue.extend(item.values())
    return None


def bytes_to_readable(obj: Any) -> Any:
    """將 bytes 轉為可讀字串或 hex。"""
    if isinstance(obj, dict):
        return {k: bytes_to_readable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [bytes_to_readable(x) for x in obj]
    elif isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    else:
        return obj


def _decode_packed_varint(data: Any, field_typedef: Any = None) -> Any:
    """解碼 packed varint，並嘗試修復被 blackbox 誤判為 dict/str 的情況。"""
    if isinstance(data, list):
        out = []
        for item in data:
            res = _decode_packed_varint(item, field_typedef)
            if isinstance(res, list):
                out.extend(res)
            else:
                out.append(res)
        return out

    raw_bytes = b""
    if isinstance(data, (bytes, bytearray)):
        raw_bytes = data
    elif isinstance(data, str):
        raw_bytes = data.encode("utf-8")
    elif isinstance(data, dict):
        # 若盲解誤判為子物件，使用其預測的 typedef 將其重新編碼回原始 bytes
        if isinstance(field_typedef, dict) and "message_typedef" in field_typedef:
            try:
                raw_bytes = blackboxprotobuf.encode_message(
                    data, field_typedef["message_typedef"]
                )
            except Exception:
                return data
        else:
            return data
    else:
        return data

    result = []
    index = 0
    while index < len(raw_bytes):
        try:
            val, index = _decode_varint(raw_bytes, index)
            # 處理 64-bit 負數 (Two's complement)，例如 -1
            if val >= (1 << 63):
                val -= 1 << 64
            result.append(val)
        except Exception:
            break
    return result


def _decode_double(data: Any) -> Any:
    """將被誤解為整數的 64-bit 數值還原為 float (double)。"""
    if isinstance(data, list):
        return [_decode_double(item) for item in data]
    if isinstance(data, int):
        try:
            # 將整數轉為 8-byte (Little Endian)，再依 IEEE-754 double 解開
            return struct.unpack("<d", struct.pack("<Q", data & 0xFFFFFFFFFFFFFFFF))[0]
        except Exception:
            return data
    elif isinstance(data, float):
        return data
    return data


def _normalize_grid_cell_defaults(data: Any, schema: dict[str, Any]) -> Any:
    """只在 grid cell schema 下補齊缺失的 code 預設值，避免 position-only cell 失真。"""
    if not isinstance(data, dict):
        return data
    if schema.get("1") != "position" or schema.get("2") != "code":
        return data
    if "position" in data and "code" not in data:
        data["code"] = 0
    return data


def _apply_protobuf_schema(
    data: Any, schema: dict[str, Any], typedef: Any = None
) -> Any:
    """將 blackboxprotobuf 的盲解結果轉成專案內可讀欄位名稱。"""
    if isinstance(data, list):
        return [_apply_protobuf_schema(item, schema, typedef) for item in data]
    elif isinstance(data, dict):
        new_data = {}
        for k, v in data.items():
            str_k = str(k)
            # blackboxprotobuf 偶爾會把同一欄拆成 `4-1` 這種格式，這裡先還原成基礎欄位號。
            base_k = str_k.split("-")[0]

            field_typedef = None
            if isinstance(typedef, dict):
                if str_k in typedef:
                    field_typedef = typedef[str_k]
                elif base_k in typedef:
                    field_typedef = typedef[base_k]

            sub_msg_typedef = None
            if isinstance(field_typedef, dict) and "message_typedef" in field_typedef:
                sub_msg_typedef = field_typedef["message_typedef"]

            if base_k in schema:
                mapping = schema[base_k]
                if isinstance(mapping, tuple):
                    new_key, sub_schema = mapping
                    if sub_schema == "packed_varint":
                        processed_v = _decode_packed_varint(v, field_typedef)
                    elif sub_schema == "double":
                        processed_v = _decode_double(v)
                    else:
                        processed_v = _apply_protobuf_schema(
                            v, sub_schema, sub_msg_typedef
                        )
                else:
                    new_key = mapping
                    processed_v = _apply_protobuf_schema(v, {}, sub_msg_typedef)

                # 若同一欄被盲解成多個片段，這裡再合併回單一 list，方便後續寫 log/CSV。
                if new_key in new_data:
                    existing = new_data[new_key]
                    if not isinstance(existing, list):
                        existing = [existing]
                    if isinstance(processed_v, list):
                        existing.extend(processed_v)
                    else:
                        existing.append(processed_v)
                    new_data[new_key] = existing
                else:
                    new_data[new_key] = processed_v
            else:
                new_data[k] = _apply_protobuf_schema(v, {}, sub_msg_typedef)
        return _normalize_grid_cell_defaults(new_data, schema)
    return data


def decode_protobuf_to_json(
    response_b64: str,
    pid: Optional[str] = None,
    event_name: Optional[str] = None,
) -> Optional[str]:
    """將 base64 protobuf 轉成 JSON 字串，並套用遊戲專用 schema 讓 log 更好讀。"""
    try:
        decoded = base64.b64decode(response_b64)
        msg, typedef = blackboxprotobuf.decode_message(decoded)

        if pid and event_name:
            schema = _PROTO_SCHEMAS.get(pid, {}).get(event_name)
            if schema:
                # 先套 schema 再轉字串，避免 bytes/raw dict 在 JSON 化過程中失真。
                msg = _apply_protobuf_schema(msg, schema, typedef)

        msg_clean = bytes_to_readable(msg)

        return json.dumps(msg_clean, indent=4, ensure_ascii=False)
    except Exception:
        return None


def _flatten_dict(
    d: dict[str, Any], parent_key: str = "", sep: str = "_"
) -> dict[str, Any]:
    """將巢狀 dict 展平成單層欄位。"""
    items: list[Tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            items.append((new_key, json.dumps(v, ensure_ascii=False)))
        else:
            items.append((new_key, v))
    return dict(items)


def write_csv_log(
    run_dir: Path, loginname: str, event: str, tag: str, data: Any, pid: str, max_bytes: int = 104857600
) -> None:
    """將 decoded 資料寫入 CSV 檔。"""
    csv_path = run_dir / f"{loginname}_ack.csv"

    # 每次寫入前檢查，若超過設定的大小時進行切檔處理
    if max_bytes > 0 and csv_path.exists():
        try:
            if csv_path.stat().st_size >= max_bytes:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                rotated_path = csv_path.with_name(f"{csv_path.stem}_{timestamp}{csv_path.suffix}")
                csv_path.rename(rotated_path)
        except OSError:
            pass

    if event == "summary" and isinstance(data, dict):
        start_time_str = data.get("start_time") or ""
        end_time_str = data.get("end_time") or ""
        duration = ""
        if start_time_str and end_time_str:
            try:
                start_dt = datetime.fromisoformat(start_time_str)
                end_dt = datetime.fromisoformat(end_time_str)
                duration = f"{(end_dt - start_dt).total_seconds():.6f}"
            except ValueError:
                pass

        total_spin_win = data.get("total_spin_win", 0.0)
        total_free_spin_win = data.get("total_free_spin_win", 0.0)
        spin_count = data.get("spin_count", 0)
        bet_amount = data.get("bet_amount", 0.0)

        total_bet = bet_amount * spin_count
        rtp = (
            f"{((total_spin_win + total_free_spin_win) / total_bet) * 100:.6f}"
            if total_bet > 0
            else "0.000000"
        )

        line = (
            f"{datetime.now().isoformat()},"
            f" spin_count,{spin_count},"
            f" free_spin_count,{data.get('free_spin_count', 0)},"
            f" total_spin_win_amount,{total_spin_win},"
            f" total_free_spin_win_amount,{total_free_spin_win},"
            f" RTP(%),{rtp},"
            f" start_time,{start_time_str},"
            f" end_time,{end_time_str},"
            f" duration_seconds,{duration}\n"
        )
        with csv_path.open("a", encoding="utf-8") as f:
            f.write(line)

        total_summary_path = run_dir / "total_summary.csv"
        total_file_exists = total_summary_path.exists()
        with total_summary_path.open("a", encoding="utf-8") as f:
            if not total_file_exists:
                f.write(
                    "account,spin_count,free_spin_count,total_spin_win_amount,total_free_spin_win_amount,RTP(%),send_start_time,receive_end_time,duration_seconds\n"
                )
            f.write(
                f"{loginname},"
                f"{spin_count},"
                f"{data.get('free_spin_count', 0)},"
                f"{total_spin_win},"
                f"{total_free_spin_win},"
                f"{rtp},"
                f"{start_time_str},"
                f"{end_time_str},"
                f"{duration}\n"
            )
        return

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return
    if not isinstance(data, dict):
        return

    profile = SLOT_PROFILES.get(pid, {})
    symbol_map = profile.get("symbol_map", {})

    unmapped_symbol_codes: list[dict[str, Any]] = []

    def _collect_unmapped_cells(value: Any) -> None:
        if isinstance(value, dict):
            if "position" in value and "code" in value:
                code = value.get("code")
                if code is not None and str(code) not in symbol_map:
                    unmapped_symbol_codes.append(
                        {"position": value.get("position"), "code": code}
                    )
            for nested in value.values():
                _collect_unmapped_cells(nested)
            return
        if isinstance(value, list):
            for item in value:
                _collect_unmapped_cells(item)

    _collect_unmapped_cells(data)

    # 移除不需要寫入的欄位，並攤平資料
    data_to_write = data.copy()
    data_to_write.pop("scatter", None)
    flat_data = _flatten_dict(data_to_write)

    # 主動提取要置前的欄位，並處理預設值與巢狀屬性攤平的問題
    payout = flat_data.pop(
        "total_payout",
        flat_data.pop("total_payout_value", flat_data.pop("total_payout_1", 0.0)),
    )
    bet = flat_data.pop("bet", flat_data.pop("bet_value", flat_data.pop("bet_1", "")))

    row_data = {
        "_ts": datetime.now().isoformat(),
        "_event": event,
        "_tag": tag,
        "spin_id": flat_data.pop("spin_id", ""),
        "grid": flat_data.pop("grid", ""),
        "payout": payout if payout is not None else 0.0,
        "bet": bet if bet is not None else "",
        "unmapped_symbol_codes": (
            json.dumps(unmapped_symbol_codes, ensure_ascii=False)
            if unmapped_symbol_codes
            else ""
        ),
        **flat_data,
    }

    fieldnames = list(row_data.keys())
    file_exists = csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_data)
