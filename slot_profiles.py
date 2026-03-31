from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - explicit runtime dependency
    yaml = None

# 這裡保留 Python 端的共用常數，讓多個遊戲 profile 可以用簡短名稱引用。
PAYLINES_3X5 = [
    [2, 2, 2, 2, 2],
    [1, 1, 1, 1, 1],
    [3, 3, 3, 3, 3],
    [1, 2, 3, 2, 1],
    [3, 2, 1, 2, 3],
    [2, 1, 1, 1, 2],
    [2, 3, 3, 3, 2],
    [1, 1, 2, 3, 3],
    [3, 3, 2, 1, 1],
    [2, 1, 2, 3, 1],
    [2, 3, 2, 1, 2],
    [1, 2, 2, 2, 1],
    [3, 2, 2, 2, 3],
    [1, 2, 1, 2, 1],
    [3, 2, 3, 2, 3],
    [2, 2, 1, 2, 2],
    [2, 2, 3, 2, 2],
    [1, 1, 3, 1, 1],
    [3, 3, 1, 3, 3],
    [1, 3, 3, 3, 1],
    [3, 1, 1, 1, 3],
    [2, 1, 3, 1, 3],
    [2, 3, 1, 3, 2],
    [1, 3, 1, 3, 1],
    [3, 1, 3, 1, 3],
]

PAYLINES_5X5 = [
    [1, 1, 1, 1, 1],
    [2, 2, 2, 2, 2],
    [3, 3, 3, 3, 3],
    [4, 4, 4, 4, 4],
    [5, 5, 5, 5, 5],
    [1, 2, 3, 2, 1],
    [5, 4, 3, 4, 5],
    [3, 2, 1, 2, 3],
    [3, 4, 5, 4, 3],
    [1, 2, 3, 4, 5],
    [5, 4, 3, 2, 1],
    [2, 3, 4, 5, 4],
    [4, 3, 2, 1, 2],
    [1, 2, 1, 2, 1],
    [5, 4, 5, 4, 5],
    [2, 1, 2, 1, 2],
    [4, 5, 4, 5, 4],
    [2, 3, 2, 3, 2],
    [4, 3, 4, 3, 4],
    [3, 2, 3, 2, 3],
    [3, 4, 3, 4, 3],
    [1, 2, 3, 4, 3],
    [5, 4, 3, 2, 3],
    [2, 3, 4, 3, 2],
    [4, 3, 2, 3, 4],
]


_GAME_PROFILES_PATH = Path(__file__).with_name("game_profiles.yaml")
_PAYLINES_REF = {
    "PAYLINES_3X5": PAYLINES_3X5,
    "PAYLINES_5X5": PAYLINES_5X5,
}


def _load_game_profiles_raw(path: Path) -> dict[str, Any]:
    """讀取真正的 YAML 設定檔，失敗時提供新手看得懂的錯誤。"""
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to load game_profiles.yaml comments. "
            "Install it with: pip install PyYAML"
        )

    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("game_profiles.yaml must be a mapping")
    return loaded


def _normalize_paytable(paytable: Any) -> Any:
    """把 YAML 裡的字串數字 key 轉回 Python int，方便 evaluator 查表。"""
    if not isinstance(paytable, dict):
        return paytable
    normalized: dict[str, Any] = {}
    for symbol, payouts in paytable.items():
        if not isinstance(payouts, dict):
            normalized[str(symbol)] = payouts
            continue
        symbol_payouts: dict[Any, Any] = {}
        for count_key, payout in payouts.items():
            if isinstance(count_key, str) and count_key.isdigit():
                symbol_payouts[int(count_key)] = payout
            else:
                symbol_payouts[count_key] = payout
        normalized[str(symbol)] = symbol_payouts
    return normalized


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """把 YAML 內易寫但不易用的結構，轉成 runtime 方便讀的格式。"""
    normalized = dict(profile)

    # YAML 內用 list 表示 grid，比 tuple 好寫；載入後再轉成 Python 慣用格式。
    grid = normalized.get("grid")
    if isinstance(grid, list) and len(grid) == 2:
        normalized["grid"] = (int(grid[0]), int(grid[1]))

    # 遊戲設定只需填引用名稱，實際線型表仍由 Python 共用常數提供。
    paylines_ref = normalized.pop("paylines_ref", None)
    if isinstance(paylines_ref, str):
        normalized["paylines"] = _PAYLINES_REF[paylines_ref]

    # paytable / symbol_map 在 evaluator 內會頻繁查詢，先做一次鍵值正規化。
    if "paytable" in normalized:
        normalized["paytable"] = _normalize_paytable(normalized["paytable"])

    if "symbol_map" in normalized and isinstance(normalized["symbol_map"], dict):
        normalized["symbol_map"] = {
            str(key): value for key, value in normalized["symbol_map"].items()
        }

    if "board_key" in normalized and isinstance(normalized["board_key"], list):
        normalized["board_key"] = list(normalized["board_key"])

    return normalized


def _load_slot_profiles() -> Dict[str, Dict[str, Any]]:
    """載入 YAML 後，逐個 profile 做正規化，輸出 runtime 共用設定。"""
    raw_profiles = _load_game_profiles_raw(_GAME_PROFILES_PATH)
    return {
        str(pid): _normalize_profile(profile)
        for pid, profile in raw_profiles.items()
        if isinstance(profile, dict)
    }


SLOT_PROFILES: Dict[str, Dict[str, Any]] = _load_slot_profiles()

# BEGIN AUTO-GENERATED PROTO SCHEMAS
_GRID_CELL = {"1": "position", "2": "code"}

_MONEY = {
    "1": "value",
    "2": "currency_code",
    "3": "display_scale",
    "4": "compact_notation",
}

_PAY_LINE = {
    "1": "id",
    "2": ("positions", "packed_varint"),
    "3": ("hit_positions", "packed_varint"),
    "4": ("payout", "double"),
}

_SCATTER_SS01 = {
    "1": "count",
    "2": ("payout", "double"),
    "3": "triggered",
}

_SPIN_RET_SS01 = {
    "1": "spin_id",
    "2": "spin_type",
    "3": ("grid", _GRID_CELL),
    "4": ("pay_lines", _PAY_LINE),
    "5": ("scatter", _SCATTER_SS01),
    "6": ("total_payout", "double"),
    "7": ("bet", "double"),
}

_FREE_SPIN_TRIGGERED_SS01 = {
    "1": "triggered_spin_id",
    "2": "triggered_type",
    "3": "tokens",
    "4": ("bet", "double"),
}

_FREE_SPIN_TRIGGERED_MONEY = {
    "1": "triggered_spin_id",
    "2": "triggered_type",
    "3": "tokens",
    "4": ("bet", _MONEY),
}

_BALANCE_CHANGED_DOUBLE = {"1": ("balance", "double")}

_BALANCE_CHANGED_MONEY = {"1": ("balance", _MONEY)}

_ALERT_SS01 = {
    "1": "code",
    "2": "type",
    "3": "message_en_US",
    "4": "message_zh_CN",
    "5": "message_zh_TW",
}

_WIN_SYMBOL_SS02 = {
    "1": "code",
    "2": "count",
    "3": ("payout", _MONEY),
}

_SCENARIOS_SS02 = {
    "1": "id",
    "2": ("grid", _GRID_CELL),
    "3": ("win_symbol", _WIN_SYMBOL_SS02),
}

_MULTIPLIER_SS02 = {"1": "code", "2": ("value", "double")}

_SCATTER_SS02 = {"1": "count", "2": "triggered"}

_SPIN_RET_SS02 = {
    "1": "spin_id",
    "2": "spin_type",
    "3": ("scenarios", _SCENARIOS_SS02),
    "4": ("multiplier", _MULTIPLIER_SS02),
    "5": ("scatter", _SCATTER_SS02),
    "6": ("total_payout", _MONEY),
    "7": ("bet", _MONEY),
}

_ALERT_SS02 = {
    "1": "code",
    "2": "type",
    "3": "message_en_US",
    "4": "message_zh_CN",
    "5": "message_zh_TW",
    "6": "message_id_ID",
    "7": "message_vi_VN",
}

_SCATTER_SS03 = {
    "1": "code",
    "2": "count",
    "3": "triggered",
    "4": "triggerCount",
}

_WIN_SYMBOL_SS03 = {
    "1": "code",
    "2": "column",
    "3": "ways",
    "4": "multiplier",
    "5": ("payout", _MONEY),
    "6": ("totalPayout", _MONEY),
}

_SCENARIOS_SS03 = {
    "1": "id",
    "2": ("grid", _GRID_CELL),
    "3": ("win_symbol", _WIN_SYMBOL_SS03),
}

_SPIN_RET_SS03 = {
    "1": "spin_id",
    "2": "spin_type",
    "3": ("GridStop", "packed_varint"),
    "4": ("scenarios", _SCENARIOS_SS03),
    "5": ("scatter", _SCATTER_SS03),
    "6": ("total_payout", _MONEY),
    "7": ("bet", _MONEY),
}
# END AUTO-GENERATED PROTO SCHEMAS

_SPIN_REQ_SS01 = {"1": "bet_amount"}
_SPIN_REQ_MONEY = {
    "1": "bet_amount",
    "2": "currency",
    "3": "display_scale",
    "4": "compact_notation",
}
_SPIN_REQ_SS02 = {"1": ("data", _SPIN_REQ_MONEY)}
_FREE_SPIN_REQ = {"1": "token"}

_PROTO_SCHEMAS = {
    "SS01": {
        "client:spin": _SPIN_RET_SS01,
        "client:spin:req": _SPIN_REQ_SS01,
        "client:free_spin": _SPIN_RET_SS01,
        "client:free_spin:req": _FREE_SPIN_REQ,
        "server:free_spin:triggered": _FREE_SPIN_TRIGGERED_SS01,
        "server:mock_free_spin:triggered": _FREE_SPIN_TRIGGERED_SS01,
        "server:balance:changed": _BALANCE_CHANGED_DOUBLE,
        "server:alert": _ALERT_SS01,
    },
    "SS02": {
        "client:spin": _SPIN_RET_SS02,
        "client:spin:req": _SPIN_REQ_SS02,
        "client:free_spin": _SPIN_RET_SS02,
        "client:free_spin:req": _FREE_SPIN_REQ,
        "server:free_spin:triggered": _FREE_SPIN_TRIGGERED_MONEY,
        "server:balance:changed": _BALANCE_CHANGED_MONEY,
        "server:alert": _ALERT_SS02,
    },
    "SS03": {
        "client:spin": _SPIN_RET_SS03,
        "client:spin:req": _SPIN_REQ_SS02,
        "client:free_spin": _SPIN_RET_SS03,
        "client:free_spin:req": _FREE_SPIN_REQ,
        "server:free_spin:triggered": _FREE_SPIN_TRIGGERED_MONEY,
        "server:balance:changed": _BALANCE_CHANGED_MONEY,
        "server:alert": _ALERT_SS01,
    },
    "FM01": {
        "client:bullet:hit:req": {"1": "fish_id", "2": "bullet_hit_type"},
    },
}
_PROTO_SCHEMAS["SS01A"] = _PROTO_SCHEMAS["SS01"]
