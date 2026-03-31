import re
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parent.parent
SLOT_PROFILES_PATH = ROOT / "slot_profiles.py"
PROTO_FILES = {
    "SS01": ROOT / "ss01_protobuf.cpp",
    "SS02": ROOT / "ss02_protobuf.cpp",
    "SS03": ROOT / "ss03_protobuf.cpp",
}

MARKER_START = "# BEGIN AUTO-GENERATED PROTO SCHEMAS"
MARKER_END = "# END AUTO-GENERATED PROTO SCHEMAS"

SCALAR_TYPES = {"int32", "int64", "string", "bool", "double"}
PACKED_VARINT_NAMES = {"positions", "hit_positions", "GridStop"}

ALIAS_MAP = {
    ("shared", "GridCell"): "_GRID_CELL",
    ("shared", "Money"): "_MONEY",
    ("SS01", "PayLine"): "_PAY_LINE",
    ("SS01", "Scatter"): "_SCATTER_SS01",
    ("SS01", "SpinRet"): "_SPIN_RET_SS01",
    ("SS01", "FreeSpinTriggered"): "_FREE_SPIN_TRIGGERED_SS01",
    ("SS01", "BalanceChanged"): "_BALANCE_CHANGED_DOUBLE",
    ("SS01", "Alert"): "_ALERT_SS01",
    ("SS02", "WinSymbol"): "_WIN_SYMBOL_SS02",
    ("SS02", "Scenarios"): "_SCENARIOS_SS02",
    ("SS02", "Multiplier"): "_MULTIPLIER_SS02",
    ("SS02", "Scatter"): "_SCATTER_SS02",
    ("SS02", "SpinRet"): "_SPIN_RET_SS02",
    ("SS02", "FreeSpinTriggered"): "_FREE_SPIN_TRIGGERED_MONEY",
    ("SS02", "BalanceChanged"): "_BALANCE_CHANGED_MONEY",
    ("SS02", "Alert"): "_ALERT_SS02",
    ("SS03", "Scatter"): "_SCATTER_SS03",
    ("SS03", "WinSymbol"): "_WIN_SYMBOL_SS03",
    ("SS03", "Scenarios"): "_SCENARIOS_SS03",
    ("SS03", "SpinRet"): "_SPIN_RET_SS03",
}

EMIT_ORDER = [
    ("shared", "GridCell"),
    ("shared", "Money"),
    ("SS01", "PayLine"),
    ("SS01", "Scatter"),
    ("SS01", "SpinRet"),
    ("SS01", "FreeSpinTriggered"),
    ("SS02", "FreeSpinTriggered"),
    ("SS01", "BalanceChanged"),
    ("SS02", "BalanceChanged"),
    ("SS01", "Alert"),
    ("SS02", "WinSymbol"),
    ("SS02", "Scenarios"),
    ("SS02", "Multiplier"),
    ("SS02", "Scatter"),
    ("SS02", "SpinRet"),
    ("SS02", "Alert"),
    ("SS03", "Scatter"),
    ("SS03", "WinSymbol"),
    ("SS03", "Scenarios"),
    ("SS03", "SpinRet"),
]


def _strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if "//" in line:
            line = line.split("//", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def _parse_proto_messages(text: str) -> dict[str, list[dict[str, Any]]]:
    messages: dict[str, list[dict[str, Any]]] = {}
    cleaned = _strip_comments(text)
    pattern = re.compile(r"message\s+(\w+)\s*\{(.*?)\}", re.DOTALL)
    field_re = re.compile(
        r"^\s*(repeated\s+)?(optional\s+)?(\w+)\s+(\w+)\s*=\s*(\d+)\s*;\s*$"
    )

    for message_name, body in pattern.findall(cleaned):
        fields: list[dict[str, Any]] = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = field_re.match(line)
            if not match:
                continue
            repeated, _optional, field_type, field_name, field_num = match.groups()
            fields.append(
                {
                    "num": field_num,
                    "type": field_type,
                    "name": field_name,
                    "repeated": bool(repeated),
                }
            )
        messages[message_name] = fields
    return messages


def _python_repr(value: Any) -> str:
    if isinstance(value, str):
        if value.startswith("_"):
            return value
        return f'"{value}"'
    if isinstance(value, tuple):
        return f"({', '.join(_python_repr(item) for item in value)})"
    if isinstance(value, dict):
        if len(value) <= 2:
            inner = ", ".join(f'{_python_repr(k)}: {_python_repr(v)}' for k, v in value.items())
            return f"{{{inner}}}"
    return repr(value)


def _shared_alias_for_type(field_type: str) -> Optional[str]:
    return ALIAS_MAP.get(("shared", field_type))


def _field_mapping(pid: str, field: dict[str, Any]) -> Any:
    field_type = field["type"]
    field_name = field["name"]
    repeated = field["repeated"]

    if repeated and field_type in {"int32", "int64"} and field_name in PACKED_VARINT_NAMES:
        return (field_name, "packed_varint")
    if field_type == "double":
        return (field_name, "double")
    if field_type in SCALAR_TYPES:
        return field_name

    shared_alias = _shared_alias_for_type(field_type)
    if shared_alias:
        return (field_name, shared_alias)

    alias = ALIAS_MAP.get((pid, field_type))
    if alias:
        return (field_name, alias)

    raise ValueError(f"unsupported field type: pid={pid}, field_type={field_type}")


def _build_schema(pid: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {field["num"]: _field_mapping(pid, field) for field in fields}


def _render_schema(name: str, schema: dict[str, Any]) -> list[str]:
    items = list(schema.items())
    if len(items) <= 2:
        inner = ", ".join(f'{_python_repr(k)}: {_python_repr(v)}' for k, v in items)
        return [f"{name} = {{{inner}}}"]

    lines = [f"{name} = {{"]
    for key, value in items:
        lines.append(f'    {_python_repr(key)}: {_python_repr(value)},')
    lines.append("}")
    return lines


def generate_schema_block() -> str:
    parsed = {pid: _parse_proto_messages(path.read_text(encoding="utf-8")) for pid, path in PROTO_FILES.items()}

    alias_to_schema: dict[str, dict[str, Any]] = {}
    for pid, message_name in EMIT_ORDER:
        source_pid = "SS02" if pid == "shared" and message_name == "Money" else pid
        if pid == "shared":
            source_pid = "SS01" if message_name == "GridCell" else "SS02"
        fields = parsed[source_pid][message_name]
        schema_pid = source_pid if pid != "shared" else source_pid
        schema = _build_schema(schema_pid, fields)
        alias = ALIAS_MAP[(pid, message_name)]
        if alias in alias_to_schema:
            if alias_to_schema[alias] != schema:
                raise ValueError(f"schema mismatch for alias {alias}")
            continue
        alias_to_schema[alias] = schema

    output_lines = [MARKER_START]
    for pid, message_name in EMIT_ORDER:
        alias = ALIAS_MAP[(pid, message_name)]
        if alias not in alias_to_schema:
            continue
        if any(line.startswith(f"{alias} =") for line in output_lines):
            continue
        output_lines.extend(_render_schema(alias, alias_to_schema[alias]))
        output_lines.append("")
    if output_lines[-1] == "":
        output_lines.pop()
    output_lines.append(MARKER_END)
    return "\n".join(output_lines)


def write_slot_profiles() -> None:
    content = SLOT_PROFILES_PATH.read_text(encoding="utf-8")
    start = content.find(MARKER_START)
    end = content.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("auto-generated markers not found in slot_profiles.py")

    end += len(MARKER_END)
    new_block = generate_schema_block()
    updated = content[:start] + new_block + content[end:]
    SLOT_PROFILES_PATH.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    write_slot_profiles()
