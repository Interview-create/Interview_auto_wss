import itertools
from typing import Any, Dict, List, Optional, Tuple

from slot_profiles import SLOT_PROFILES


def get_profile(pid: str) -> Optional[Dict[str, Any]]:
    """依 pid 取得對應配置。"""
    if pid in SLOT_PROFILES:
        return SLOT_PROFILES[pid]
    return SLOT_PROFILES.get("SS01")


def get_board_key(pid: str) -> str:
    """依 pid 取得盤面 key。"""
    profile = get_profile(pid)
    if profile and profile.get("board_key"):
        return profile["board_key"]
    return "4"


def get_mode(pid: str) -> str:
    """依 pid 取得盤面模式。"""
    profile = get_profile(pid)
    if profile and profile.get("mode"):
        return profile["mode"]
    return "payline"


def get_grid(pid: str) -> Tuple[int, int]:
    """依 pid 取得盤面尺寸，格式為 (rows, cols)。"""
    profile = get_profile(pid)
    if profile and isinstance(profile.get("grid"), tuple):
        rows, cols = profile["grid"]
        return int(rows), int(cols)
    return 5, 5


def infer_grid_from_cells(cells: List[Dict[str, Any]]) -> Tuple[int, int]:
    """從盤面 cells 推導盤面尺寸，position 採 rowcol 編碼。"""
    max_row = 0
    max_col = 0
    for cell in cells:
        pos = cell.get("position", cell.get("1", cell.get(1)))
        if pos is None:
            continue
        try:
            pos_int = int(pos)
        except (TypeError, ValueError):
            continue

        row = pos_int // 10
        col = pos_int % 10
        if col > max_col:
            max_col = col
        if row > max_row:
            max_row = row
    if max_row <= 0 or max_col <= 0:
        return 5, 5
    return max_row, max_col


def _map_symbol(code: Any, profile: Dict[str, Any]) -> Optional[str]:
    """依 profile 的 symbol_map 對應碼表。"""
    if code is None:
        return None
    symbol_map = profile.get("symbol_map", {})
    return symbol_map.get(str(code))


def _resolve_wild(profile: Dict[str, Any]) -> Optional[str]:
    """從 symbol_map 推導 wild 符號。"""
    symbol_map = profile.get("symbol_map", {})
    for value in symbol_map.values():
        if value.lower() == "wild":
            return value
    return None


def _resolve_scatter(profile: Dict[str, Any]) -> Optional[str]:
    """從 symbol_map 推導 scatter 符號。"""
    symbol_map = profile.get("symbol_map", {})
    for value in symbol_map.values():
        if value.lower() == "scatter":
            return value
    return None


def _resolve_count_on_board_payout(
    symbol_paytable: Dict[Any, Any], total_count: int
) -> int:
    """依 count_on_board 規則的區間 paytable 取得賠付。"""
    if total_count >= 12:
        return int(symbol_paytable.get("12+", 0))
    if total_count >= 10:
        return int(symbol_paytable.get("10-11", 0))
    if total_count >= 8:
        return int(symbol_paytable.get("8-9", 0))
    return 0


def _resolve_cascading_payout(
    profile: Dict[str, Any], symbol_paytable: Dict[Any, Any], total_count: int
) -> int:
    """依 profile 中的 win_rule 解析 cascading payout。"""
    win_rule = str(profile.get("win_rule", "")).strip()
    if win_rule == "count_on_board":
        return _resolve_count_on_board_payout(symbol_paytable, total_count)
    return int(symbol_paytable.get(total_count, 0))


def decode_cells_to_reels(
    cells: List[Any],
    rows: int,
    cols: int,
    profile: Dict[str, Any],
) -> List[List[Optional[str]]]:
    """將盤面 cell 轉為 reels 格式，外層為 col、內層依 row 排序。"""
    reels: List[List[Optional[str]]] = [
        [None for _ in range(rows)] for _ in range(cols)
    ]

    if not cells:
        return reels

    if not isinstance(cells[0], dict):
        for index, symbol_code in enumerate(cells):
            if symbol_code is None or symbol_code == -1:
                continue
            try:
                col = index // rows
                row = index % rows
            except (TypeError, ValueError):
                continue
            if col < 0 or col >= cols or row < 0 or row >= rows:
                continue
            symbol = _map_symbol(symbol_code, profile)
            reels[col][row] = symbol
        return reels

    for cell in cells:
        pos = cell.get("position", cell.get("1", cell.get(1)))
        symbol_code = cell.get("code", cell.get("2", cell.get(2)))
        if pos is None:
            continue
        if symbol_code == -1:
            continue
        if symbol_code is None:
            symbol_code = 0  # 解析問題先補0

        try:
            pos_int = int(pos)
        except (TypeError, ValueError):
            continue

        # dict cell 的 position 使用 rowcol，例如 56 = 第 5 列、第 6 欄
        col = pos_int % 10
        row = pos_int // 10
        if col < 1 or col > cols or row < 1 or row > rows:
            continue

        symbol = _map_symbol(symbol_code, profile)
        reels[col - 1][row - 1] = symbol

    return reels


def evaluate_line(
    reels: List[List[Optional[str]]],
    line: List[int],
    rows: int,
    cols: int,
    profile: Dict[str, Any],
) -> Optional[Tuple[str, int, int, List[Optional[str]]]]:
    """計算單條 payline 是否中獎。"""
    if len(line) != cols:
        return None

    symbols: List[Optional[str]] = []
    for reel_index, row in enumerate(line):
        if row < 1 or row > rows:
            return None
        symbols.append(reels[reel_index][row - 1])

    base_symbol: Optional[str] = None
    count = 0

    wild = _resolve_wild(profile)
    scatter = _resolve_scatter(profile)

    for s in symbols:
        if s is None:
            return None
        if base_symbol is None:
            if s == scatter:
                return None
            if s == wild:
                count += 1
                continue
            base_symbol = s
            count += 1
            continue
        if s == base_symbol or s == wild:
            count += 1
        else:
            break

    if base_symbol is None:
        return None
    if count >= 3:
        payout = profile.get("paytable", {}).get(base_symbol, {}).get(count)
        if payout:
            return base_symbol, count, payout, symbols
    return None


def evaluate_scatter(
    reels: List[List[Optional[str]]],
    rows: int,
    cols: int,
    profile: Dict[str, Any],
) -> Optional[int]:
    """計算 scatter 數量。"""
    count = 0
    scatter = _resolve_scatter(profile)
    if not scatter:
        return None
    for col in range(cols):
        for row in range(rows):
            if reels[col][row] == scatter:
                count += 1
    if count > 0:
        return count
    return None


def evaluate_spin(
    reels: List[List[Optional[str]]],
    rows: int,
    cols: int,
    profile: Dict[str, Any],
    bet_amount: float = 1.0,
) -> Dict[str, Any]:
    """Payline 模式的整體評分。"""
    total_win = 0
    line_wins: List[Dict[str, Any]] = []

    paylines = profile.get("paylines", [])
    for idx, line in enumerate(paylines, start=1):
        result = evaluate_line(reels, line, rows, cols, profile)
        if result:
            symbol, count, payout, path = result
            total_win += payout * (bet_amount / len(paylines))
            line_wins.append(
                {
                    "line": idx,
                    "symbol": symbol,
                    "count": count,
                    "payout": payout,
                    "path": path,
                }
            )

    scatter_count = evaluate_scatter(reels, rows, cols, profile)
    scatter_result = None
    if scatter_count:
        scatter_result = {"count": scatter_count}

    return {
        "total_win": round(total_win, 4),
        "line_wins": line_wins,
        "scatter": scatter_result,
        "reels": reels,
    }


def evaluate_payline_spin(
    reels: List[List[Optional[str]]],
    rows: int,
    cols: int,
    profile: Dict[str, Any],
    bet_amount: float = 1.0,
) -> Dict[str, Any]:
    """Payline 模式入口。"""
    return evaluate_spin(reels, rows, cols, profile, bet_amount)


def evaluate_cascading_spin(
    reels: List[List[Optional[str]]],
    rows: int,
    cols: int,
    profile: Dict[str, Any],
    pid: str,
    bet_amount: float = 1.0,
) -> Dict[str, Any]:
    """Cascading 模式入口。"""
    grid = [[symbol if symbol is not None else "" for symbol in reel] for reel in reels]
    paytable = profile.get("paytable", {})
    min_match = profile.get("min_match", 3)
    cascading_wins: List[Dict[str, Any]] = []
    cascading_total_payout = 0

    if pid == "SS02":
        min_cluster_count = max(int(profile.get("min_cluster_count", 1)), 1)
        for symbol, symbol_paytable in paytable.items():
            column_counts = [sum(1 for item in col if item == symbol) for col in grid]
            total_count = sum(column_counts)
            if total_count < min_cluster_count:
                continue
            positions = [
                (row_index + 1) * 10 + (col_index + 1)
                for col_index, col in enumerate(grid)
                for row_index, item in enumerate(col)
                if item == symbol
            ]

            base_payout = _resolve_cascading_payout(
                profile, symbol_paytable, total_count
            )
            total_payout = base_payout * (bet_amount / 20)
            cascading_wins.append(
                {
                    "symbol": symbol,
                    "count": total_count,
                    "column_counts": column_counts,
                    "positions": positions,
                    "base_payout": base_payout,
                    "payout": round(total_payout, 4),
                }
            )
            cascading_total_payout += total_payout

        return {
            "total_win": 0,
            "line_wins": [],
            "scatter": None,
            "reels": reels,
            "cascading_wins": cascading_wins,
            "cascading_total_payout": round(cascading_total_payout, 4),
        }

    for symbol, symbol_paytable in paytable.items():
        ways = find_win_ways_max(grid, symbol, min_match=min_match)
        if not ways:
            continue
        max_match = max(w["match"] for w in ways)
        payout = symbol_paytable.get(max_match, 0)
        cascading_wins.append(
            {
                "symbol": symbol,
                "match": max_match,
                "payout": payout,
                "ways": ways,
            }
        )
        cascading_total_payout += payout * bet_amount
    return {
        "total_win": 0,
        "line_wins": [],
        "scatter": None,
        "reels": reels,
        "cascading_wins": cascading_wins,
        "cascading_total_payout": round(cascading_total_payout, 4),
    }


def evaluate_spin_from_cells(
    cells: List[Dict[str, Any]],
    rows: int,
    cols: int,
    pid: str,
    mode: str,
    bet_amount: float = 1.0,
) -> Dict[str, Any]:
    """盤面評分入口，依模式分流。"""
    profile = get_profile(pid)
    if not profile:
        return {
            "total_win": 0,
            "line_wins": [],
            "scatter": None,
            "reels": [],
        }
    reels = decode_cells_to_reels(cells, rows, cols, profile)
    if mode == "cascading":
        return evaluate_cascading_spin(reels, rows, cols, profile, pid, bet_amount)
    return evaluate_payline_spin(reels, rows, cols, profile, bet_amount)


def find_win_ways_max(
    grid: List[List[str]],
    symbol: str,
    min_match: int = 3,
) -> List[Dict[str, Any]]:
    """找出指定符號的最大連續中獎 ways。"""
    reels = len(grid)
    win_results_all: List[Dict[str, Any]] = []

    symbol_rows = [[i + 1 for i, s in enumerate(col) if s == symbol] for col in grid]

    for match_count in range(min_match, reels + 1):
        for way_prefix in itertools.product(*symbol_rows[:match_count]):
            ways_row = way_prefix + tuple(None for _ in range(reels - match_count))
            symbols_seq = [symbol] * match_count
            full_symbols = [
                grid[i][r - 1] for i, r in enumerate(ways_row) if r is not None
            ]
            win_results_all.append(
                {
                    "way": ways_row,
                    "symbol": symbol,
                    "match": match_count,
                    "symbols": symbols_seq,
                    "full_symbols": full_symbols,
                }
            )

    if not win_results_all:
        return []
    max_match = max(w["match"] for w in win_results_all)
    win_results_max = [w for w in win_results_all if w["match"] == max_match]

    return win_results_max
