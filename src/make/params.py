"""Deriving the command line from the function signature.

The rule is positional in the signature, positional on the command line;
keyword-only in the signature, an option on the command line:

    def publish(path: Path, *, track: Literal["alpha", "prod"] = "alpha",
                dry: bool = False, locale: list[str] = []) -> None:

    mk publish ./app.aab --track prod --locale es-ES --locale en-US --dry

There is no second schema to keep in sync with the function, which is the class
of drift that makes a `just` variable and the task that reads it disagree.
Anything the signature cannot express -- a short flag, help text, an environment
fallback -- attaches through `Annotated[..., arg(...)]` rather than by moving
the declaration somewhere else.
"""

from __future__ import annotations

import difflib
import enum
import inspect
import os
import types
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from .errors import TaskError, UsageError

__all__ = ["Arg", "Param", "arg", "build_params", "parse_args", "render_usage"]


@dataclass(frozen=True)
class Arg:
    """Extra command-line metadata for one parameter."""

    short: str | None = None
    help: str | None = None
    env: str | None = None
    metavar: str | None = None
    name: str | None = None


def arg(
    short: str | None = None,
    *,
    help: str | None = None,
    env: str | None = None,
    metavar: str | None = None,
    name: str | None = None,
) -> Arg:
    """`Annotated[int, arg("-p", help="dev port", env="MAKE_PORT")]`."""
    if short and not short.startswith("-"):
        short = "-" + short
    return Arg(short=short, help=help, env=env, metavar=metavar, name=name)


Kind = Literal["positional", "option", "varargs"]


@dataclass
class Param:
    """One parameter, and how it appears on the command line."""

    name: str
    kind: Kind
    convert: Callable[[str], Any]
    type_label: str
    default: Any = inspect.Parameter.empty
    is_flag: bool = False
    repeat: bool = False
    choices: tuple[str, ...] | None = None
    help: str = ""
    short: str | None = None
    env: str | None = None
    cli_name: str = ""
    metavar: str = ""

    @property
    def required(self) -> bool:
        return self.default is inspect.Parameter.empty and self.kind != "varargs"

    @property
    def flag(self) -> str:
        return f"--{self.cli_name}"

    @property
    def negative_flag(self) -> str:
        return f"--no-{self.cli_name}"


# --------------------------------------------------------------------------
# Type -> converter
# --------------------------------------------------------------------------


def _bool_from_str(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("1", "true", "yes", "y", "on"):
        return True
    if lowered in ("0", "false", "no", "n", "off"):
        return False
    raise ValueError(f"expected true or false, got {text!r}")


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) != len(get_args(annotation)):
            return (args[0] if len(args) == 1 else annotation), True
    return annotation, False


def _converter(
    annotation: Any, param_name: str
) -> tuple[Callable[[str], Any], str, tuple[str, ...] | None, bool]:
    """-> (convert, label, choices, repeat)."""
    annotation, _optional = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin in (list, set, tuple, Sequence):
        inner = get_args(annotation)
        item = inner[0] if inner else str
        convert, label, choices, _ = _converter(item, param_name)
        return convert, f"{label}...", choices, True

    if origin is Literal:
        options = tuple(str(a) for a in get_args(annotation))
        types_in = {type(a) for a in get_args(annotation)}
        base = types_in.pop() if len(types_in) == 1 else str

        def convert_literal(text: str, _base: Any = base, _options: tuple[str, ...] = options) -> Any:
            if text not in _options:
                raise ValueError(f"expected one of {', '.join(_options)}")
            return _base(text) if _base is not str else text

        return convert_literal, "|".join(options), options, False

    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        options = tuple(member.name for member in annotation)

        def convert_enum(text: str, _enum: Any = annotation) -> Any:
            try:
                return _enum[text]
            except KeyError:
                try:
                    return _enum(text)
                except ValueError as exc:
                    raise ValueError(f"expected one of {', '.join(options)}") from exc

        return convert_enum, "|".join(options), options, False

    if annotation is bool:
        return _bool_from_str, "bool", None, False
    if annotation is int:
        return int, "int", None, False
    if annotation is float:
        return float, "float", None, False
    if annotation is Path:
        return Path, "path", None, False
    if annotation in (str, inspect.Parameter.empty, Any) or annotation is None:
        return str, "str", None, False
    if callable(annotation):
        return annotation, getattr(annotation, "__name__", "value"), None, False

    raise TaskError(f"parameter {param_name!r} has an unsupported annotation: {annotation!r}")


# --------------------------------------------------------------------------
# Signature -> params
# --------------------------------------------------------------------------


def _split_annotated(annotation: Any) -> tuple[Any, Arg | None]:
    if get_origin(annotation) is typing.Annotated:
        base, *extras = get_args(annotation)
        for extra in extras:
            if isinstance(extra, Arg):
                return base, extra
        return base, None
    return annotation, None


def build_params(fn: Callable[..., Any], *, doc_help: dict[str, str] | None = None) -> list[Param]:
    """Inspect `fn` and describe its command line."""
    signature = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:  # pragma: no cover - forward refs we cannot resolve
        hints = getattr(fn, "__annotations__", {})
    doc_help = doc_help or {}

    params: list[Param] = []
    for name, sig_param in signature.parameters.items():
        if sig_param.kind is inspect.Parameter.VAR_KEYWORD:
            raise TaskError(
                f"task {fn.__name__!r} declares **{name}: a task cannot take arbitrary "
                "keyword arguments, because there is no way to present them on a command line"
            )

        annotation, meta = _split_annotated(hints.get(name, sig_param.annotation))
        meta = meta or Arg()

        if sig_param.kind is inspect.Parameter.VAR_POSITIONAL:
            convert, label, _choices, _ = _converter(annotation, name)
            params.append(
                Param(
                    name=name,
                    kind="varargs",
                    convert=convert,
                    type_label=label,
                    default=(),
                    help=meta.help or doc_help.get(name, ""),
                    cli_name=name.replace("_", "-"),
                    metavar=meta.metavar or (name.replace("_", "-").upper() + "..."),
                )
            )
            continue

        convert, label, choices, repeat = _converter(annotation, name)
        kind: Kind = "option" if sig_param.kind is inspect.Parameter.KEYWORD_ONLY else "positional"
        default = sig_param.default
        is_flag = annotation is bool and kind == "option"

        if repeat and default is inspect.Parameter.empty:
            default = []

        cli_name = (meta.name or name).replace("_", "-").strip("-")
        params.append(
            Param(
                name=name,
                kind=kind,
                convert=convert,
                type_label=label,
                default=default,
                is_flag=is_flag,
                repeat=repeat,
                choices=choices,
                help=meta.help or doc_help.get(name, ""),
                short=meta.short,
                env=meta.env,
                cli_name=cli_name,
                metavar=meta.metavar or cli_name.upper(),
            )
        )

    seen_varargs = False
    for param in params:
        if seen_varargs and param.kind == "positional":
            raise TaskError("positional parameters cannot follow *args")
        seen_varargs = seen_varargs or param.kind == "varargs"
    return params


# --------------------------------------------------------------------------
# Command line -> call arguments
# --------------------------------------------------------------------------


@dataclass
class ParsedCall:
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)


def _convert(param: Param, raw: str, where: str) -> Any:
    try:
        return param.convert(raw)
    except (ValueError, TypeError) as exc:
        raise UsageError(f"{where}: invalid value {raw!r} -- {exc}") from exc


def parse_args(params: Sequence[Param], argv: Sequence[str], *, task: str) -> ParsedCall:
    """Turn the tokens after a task name into positional/keyword arguments."""
    options = {p.flag: p for p in params if p.kind == "option"}
    negatives = {p.negative_flag: p for p in params if p.kind == "option" and p.is_flag}
    shorts = {p.short: p for p in params if p.kind == "option" and p.short}
    positionals = [p for p in params if p.kind == "positional"]
    varargs = next((p for p in params if p.kind == "varargs"), None)

    given: dict[str, Any] = {}
    positional_values: list[Any] = []
    rest: list[Any] = []
    tokens = list(argv)
    passthrough = False
    index = 0

    def take_value(param: Param, token: str, inline: str | None) -> None:
        nonlocal index
        if inline is not None:
            raw = inline
        else:
            index += 1
            if index >= len(tokens):
                raise UsageError(f"{task} {token}: expected a value ({param.type_label})")
            raw = tokens[index]
        value = _convert(param, raw, f"{task} {token}")
        if param.repeat:
            given.setdefault(param.name, []).append(value)
        else:
            given[param.name] = value

    while index < len(tokens):
        token = tokens[index]

        if passthrough:
            rest.append(token)
            index += 1
            continue

        if token == "--":
            passthrough = True
            index += 1
            continue

        if token.startswith("--"):
            name, _, inline = token.partition("=")
            param = options.get(name)
            if param is not None:
                if param.is_flag and not inline:
                    given[param.name] = True
                elif param.is_flag:
                    given[param.name] = _bool_from_str(inline)
                else:
                    take_value(param, name, inline or None)
                index += 1
                continue
            negative = negatives.get(name)
            if negative is not None:
                given[negative.name] = False
                index += 1
                continue
            raise UsageError(_unknown_option(task, name, list(options) + list(negatives)))

        if token.startswith("-") and len(token) > 1 and not _looks_negative_number(token):
            name, _, inline = token.partition("=")
            param = shorts.get(name)
            if param is None:
                raise UsageError(_unknown_option(task, name, list(shorts)))
            if param.is_flag and not inline:
                given[param.name] = True
            elif param.is_flag:
                given[param.name] = _bool_from_str(inline)
            else:
                take_value(param, name, inline or None)
            index += 1
            continue

        slot = len(positional_values)
        if slot < len(positionals):
            target = positionals[slot]
            positional_values.append(_convert(target, token, f"{task} <{target.metavar}>"))
        elif varargs is not None:
            rest.append(_convert(varargs, token, f"{task} <{varargs.metavar}>"))
        else:
            raise UsageError(
                f"{task}: unexpected argument {token!r}", hint=f"usage: {render_usage(task, params)}"
            )
        index += 1

    call = ParsedCall()

    for slot, param in enumerate(positionals):
        if slot < len(positional_values):
            call.args.append(positional_values[slot])
            continue
        from_env = _from_env(param)
        if from_env is not None:
            call.args.append(from_env)
        elif param.required:
            raise UsageError(
                f"{task}: missing required argument <{param.metavar}> ({param.type_label})",
                hint=f"usage: {render_usage(task, params)}",
            )
        else:
            call.args.append(param.default)

    if varargs is not None or rest:
        call.args.extend(rest)

    for param in params:
        if param.kind != "option":
            continue
        if param.name in given:
            call.kwargs[param.name] = given[param.name]
            continue
        from_env = _from_env(param)
        if from_env is not None:
            call.kwargs[param.name] = from_env
        elif param.required:
            raise UsageError(
                f"{task}: missing required option {param.flag} ({param.type_label})",
                hint=f"usage: {render_usage(task, params)}",
            )
    return call


def _looks_negative_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _from_env(param: Param) -> Any:
    if not param.env:
        return None
    raw = os.environ.get(param.env)
    if raw is None:
        return None
    if param.repeat:
        return [param.convert(part) for part in raw.split(os.pathsep) if part]
    return param.convert(raw)


def _unknown_option(task: str, name: str, known: Sequence[str]) -> str:
    close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
    suggestion = f" -- did you mean {close[0]}?" if close else ""
    return f"{task}: unknown option {name}{suggestion}"


def render_usage(task: str, params: Sequence[Param]) -> str:
    """A one-line usage string, used in every argument error."""
    parts = [task]
    for param in params:
        if param.kind == "positional":
            parts.append(f"<{param.metavar}>" if param.required else f"[{param.metavar}]")
        elif param.kind == "varargs":
            parts.append(f"[{param.metavar}]")
        elif param.required:
            parts.append(f"{param.flag} <{param.type_label}>")
    if any(p.kind == "option" and not p.required for p in params):
        parts.append("[options]")
    return " ".join(parts)
