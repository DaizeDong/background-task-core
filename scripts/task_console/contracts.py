"""Version-one contracts for the read-only declaration producer."""
from __future__ import annotations

from dataclasses import asdict, dataclass

SCHEMA_VERSION = 1
SCHEDULER_FIELDS = ("name", "enabled", "argv", "cwd", "timezone", "trigger", "principal",
                    "power", "timeout_seconds", "concurrency", "xml_passthrough")


class ContractError(ValueError):
    """A safe diagnostic describing the field, never echoing its supplied value."""

    def __init__(self, code: str, field: str):
        self.code, self.field = code, field
        super().__init__(f"{code}: {field}")

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field}


def require(data: dict, field: str, kind: type):
    if field not in data:
        raise ContractError("missing_field", field)
    value = data[field]
    if type(value) is not kind:
        raise ContractError("invalid_type", field)
    if kind is str and (not value.strip() or any(ord(c) < 32 for c in value)):
        raise ContractError("invalid_value", field)
    return value


def versioned(data, field: str) -> dict:
    if not isinstance(data, dict):
        raise ContractError("invalid_type", field)
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != SCHEMA_VERSION:
        raise ContractError("unsupported_schema", field)
    return data


def fields(data: dict, allowed: set[str], field: str) -> None:
    if data.keys() - allowed:
        raise ContractError("unknown_field", field)


@dataclass(frozen=True)
class TaskSpec:
    component: str
    task_id: str
    kind: str
    entrypoint: str
    read: str
    schedule_hint: dict | None
    concurrency_key: str
    name: str
    enabled: bool
    argv: list[str]
    cwd: str
    source_root: str
    timezone: str
    trigger: dict | list[dict]
    principal: dict
    power: dict
    timeout_seconds: int
    recommended_timeout_seconds: int
    concurrency: dict
    backup: bool
    category: str
    checks: list[dict]
    xml_passthrough: dict | None = None
    credential_ref: str | None = None
    installation_namespace: str | None = None
    installation_identity: dict | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
