"""Typed configuration for shared task packages.

The problem this replaces: a shared `just` file needs per-project values
(`web_port`, `android_module`), and `just` has no way to declare one as
*required*. The workaround became a house rule -- never give a consumer variable
a default, so a missing one at least produces a parse error. It works, but the
error names a variable and a line number in a file the consumer did not write,
and it costs you the ability to have sensible defaults at all.

Here a package declares a dataclass:

    @config.section("web")
    @dataclass
    class Web:
        bin: str                      # required -- no default
        port: int = 8001              # sensible default, still overridable
        watch: list[str] = field(default_factory=list)

and reads `web.port` anywhere. Values resolve, last wins:

    dataclass defaults  <  make.toml / pyproject  <  .configure()  <  MAKE_WEB_PORT

A missing required value raises at the point of use with the field, its type,
the task that wanted it, and the three places it can be set.
"""

from __future__ import annotations

import dataclasses
import os
import types
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar, get_args, get_origin

from .context import current
from .errors import ConfigError

__all__ = ["Section", "file_values", "get", "section"]

T = TypeVar("T")

_FILE_NAMES = ("make.toml", ".make.toml")
_cache: dict[Path, dict[str, Any]] = {}


def _load_toml(path: Path) -> dict[str, Any]:
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"{path}: cannot be parsed as TOML -- {exc}") from exc


def file_values(root: Path | None = None) -> dict[str, Any]:
    """Merged `[section]` tables from `make.toml`, else `[tool.make]` in pyproject."""
    base = Path(root) if root else current().root
    if base in _cache:
        return _cache[base]

    values: dict[str, Any] = {}
    for name in _FILE_NAMES:
        candidate = base / name
        if candidate.is_file():
            values = _load_toml(candidate)
            break
    else:
        pyproject = base / "pyproject.toml"
        if pyproject.is_file():
            values = _load_toml(pyproject).get("tool", {}).get("make", {}) or {}

    _cache[base] = values
    return values


def get(path: str, default: Any = None) -> Any:
    """Read a dotted path out of the config file: `config.get("web.port")`."""
    node: Any = file_values()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def reset_cache() -> None:
    _cache.clear()


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _coerce(value: Any, annotation: Any, where: str) -> Any:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin in (list, tuple, set):
        inner = get_args(annotation)
        item_type = inner[0] if inner else str
        if isinstance(value, str):
            value = [part for part in value.replace(",", " ").split() if part]
        if not isinstance(value, (list, tuple, set)):
            raise ConfigError(f"{where}: expected a list, got {type(value).__name__}")
        coerced = [_coerce(item, item_type, where) for item in value]
        return list(coerced) if origin is list else origin(coerced)

    if origin is typing.Literal:
        options = get_args(annotation)
        if value not in options:
            raise ConfigError(f"{where}: expected one of {', '.join(map(str, options))}, got {value!r}")
        return value

    if annotation is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is Path:
        return Path(str(value)).expanduser()
    if annotation is str:
        return str(value)
    if annotation in (Any, dataclasses.MISSING) or annotation is None:
        return value
    if isinstance(annotation, type) and isinstance(value, annotation):
        return value
    if callable(annotation):
        try:
            return annotation(value)
        except Exception:
            return value
    return value


def _env_name(section_name: str, field_name: str) -> str:
    clean = f"MAKE_{section_name}_{field_name}".upper()
    return "".join(ch if ch.isalnum() else "_" for ch in clean)


# --------------------------------------------------------------------------
# Section
# --------------------------------------------------------------------------


class Section(Generic[T]):
    """A declared configuration block. Attribute access resolves lazily."""

    def __init__(self, name: str, cls: type[T]) -> None:
        if not dataclasses.is_dataclass(cls):
            raise ConfigError(
                f"config.section({name!r}) expects a dataclass, got {cls.__name__}",
                hint="add @dataclass above the class",
            )
        self.name = name
        self.cls = cls
        self._explicit: dict[str, Any] = {}
        self._resolved: T | None = None
        self._resolved_for: Path | None = None

    # -- declaration-site API ---------------------------------------------

    def configure(self, **values: Any) -> Section[T]:
        """Set values from the task file. Beats config files, loses to the environment."""
        unknown = set(values) - {f.name for f in dataclasses.fields(self.cls)}
        if unknown:
            known = ", ".join(sorted(f.name for f in dataclasses.fields(self.cls)))
            raise ConfigError(
                f"{self.name}.configure(): unknown setting{'s' if len(unknown) > 1 else ''}: "
                f"{', '.join(sorted(unknown))}",
                hint=f"known settings: {known}",
            )
        self._explicit.update(values)
        self._resolved = None
        return self

    __call__ = configure

    # -- use-site API ------------------------------------------------------

    def get(self) -> T:
        """The resolved dataclass instance, built once per root directory."""
        root = current().root
        if self._resolved is not None and self._resolved_for == root:
            return self._resolved

        from_file = file_values(root).get(self.name, {}) or {}
        if not isinstance(from_file, dict):
            raise ConfigError(f"config section [{self.name}] must be a table")

        hints = typing.get_type_hints(self.cls)
        kwargs: dict[str, Any] = {}
        missing: list[tuple[str, str]] = []

        for f in dataclasses.fields(self.cls):
            annotation = hints.get(f.name, f.type)
            where = f"{self.name}.{f.name}"
            env_key = _env_name(self.name, f.name)

            if env_key in os.environ:
                kwargs[f.name] = _coerce(os.environ[env_key], annotation, where)
            elif f.name in self._explicit:
                kwargs[f.name] = _coerce(self._explicit[f.name], annotation, where)
            elif f.name in from_file:
                kwargs[f.name] = _coerce(from_file[f.name], annotation, where)
            elif f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                continue  # the dataclass supplies it
            else:
                missing.append((f.name, _label(annotation)))

        if missing:
            raise ConfigError(self._missing_message(missing))

        instance = self.cls(**kwargs)  # type: ignore[call-arg]
        self._resolved = instance
        self._resolved_for = root
        return instance

    def _missing_message(self, missing: list[tuple[str, str]]) -> str:
        lines = [f"missing required configuration for [{self.name}]:"]
        for name, label in missing:
            lines.append(f"  {self.name}.{name}  ({label})")
        lines.append("")
        lines.append("set it in any one of:")
        example = missing[0][0]
        lines.append(f"  Makefile.py   {self.name}.configure({example}=...)")
        lines.append(f"  make.toml     [{self.name}]\\n                {example} = ...")
        lines.append(f"  environment   {_env_name(self.name, example)}=...")
        return "\n".join(lines)

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self.get(), item)

    def reset(self) -> None:
        self._explicit.clear()
        self._resolved = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "resolved" if self._resolved is not None else "unresolved"
        return f"<config {self.name} ({state})>"


def _label(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (list, tuple, set):
        inner = get_args(annotation)
        return f"list of {getattr(inner[0], '__name__', 'str')}" if inner else "list"
    if origin is typing.Literal:
        return " | ".join(str(a) for a in get_args(annotation))
    return getattr(annotation, "__name__", str(annotation))


def section(name: str) -> Callable[[type[T]], Section[T]]:
    """Declare a configuration block owned by this package."""

    def decorate(cls: type[T]) -> Section[T]:
        return Section(name, cls)

    return decorate
