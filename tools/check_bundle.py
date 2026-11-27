"""Compare messenger routes/events with the reviewed API inventory.

This is a lexical inventory, not a JavaScript interpreter or a server schema.
Only literal routes, explicit HTTP methods and event switches are inspected.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
EVENT_ANCHORS = frozenset({"new_message", "new_messages", "message_edited"})
CONFIDENCE = {"hypothesis": 0, "prepared_inactive": 1, "confirmed": 2}
ROLES = {
    "index": "index-*.js",
    "route": "route-*.js",
    "media": "messengerMediaApi-*.js",
}
ROOT = Path(__file__).resolve().parents[1]
CODE_TOKEN = re.compile(r"[\w$]+|=>|&&|\|\||[^\s]")


@dataclass(frozen=True)
class Token:
    value: str
    kind: str
    offset: int


def tokens(source: str) -> list[Token]:
    """Read strings/templates and skip comments/regex without executing code."""
    result: list[Token] = []
    index = 0

    def quoted(start: int) -> tuple[str, int]:
        quote = source[start]
        pos, value = start + 1, ""
        while pos < len(source):
            char = source[pos]
            if char == quote:
                return value, pos + 1
            if char == "\\":
                pos += 1
                if pos >= len(source):
                    break
                char = source[pos]
                width = {"x": 2, "u": 4}.get(char)
                if width and re.fullmatch(
                    r"[0-9a-fA-F]+", source[pos + 1 : pos + width + 1]
                ):
                    value += chr(int(source[pos + 1 : pos + width + 1], 16))
                    pos += width + 1
                    continue
                value += {"n": "\n", "r": "\r", "t": "\t", "\n": ""}.get(char, char)
                pos += 1
                continue
            if quote == "`" and source.startswith("${", pos):
                pos, depth = pos + 2, 1
                while pos < len(source) and depth:
                    if source[pos] in "\"'`":
                        _, pos = quoted(pos)
                    elif source.startswith("/*", pos):
                        end = source.find("*/", pos + 2)
                        if end < 0:
                            raise ValueError("Незавершённый комментарий JavaScript")
                        pos = end + 2
                    elif source.startswith("//", pos):
                        end = source.find("\n", pos + 2)
                        pos = len(source) if end < 0 else end + 1
                    else:
                        depth += (source[pos] == "{") - (source[pos] == "}")
                        pos += 1
                if depth:
                    raise ValueError("Незавершённая подстановка JavaScript")
                value += "{}"
                continue
            value += char
            pos += 1
        raise ValueError("Незавершённая строка JavaScript")

    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
        elif source.startswith("//", index):
            end = source.find("\n", index + 2)
            index = len(source) if end < 0 else end + 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("Незавершённый комментарий JavaScript")
            index = end + 2
        elif char in "\"'`":
            start = index
            value, index = quoted(index)
            result.append(Token(value, "template" if char == "`" else "string", start))
        elif char == "/" and (
            not result
            or result[-1].value
            in {"(", "=", ":", ",", "[", "!", "return", "=>", "&&", "||", "?"}
        ):
            start, index, bracket = index, index + 1, False
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == "[":
                    bracket = True
                elif source[index] == "]":
                    bracket = False
                elif source[index] == "/" and not bracket:
                    index += 1
                    while index < len(source) and source[index].isalpha():
                        index += 1
                    break
                elif source[index] in "\r\n":
                    raise ValueError(
                        "Не удалось разобрать регулярное выражение JavaScript"
                    )
                index += 1
            else:
                raise ValueError("Незавершённое регулярное выражение JavaScript")
            result.append(Token("<regex>", "regex", start))
        else:
            match = CODE_TOKEN.match(source, index)
            assert match is not None
            value = match.group()
            result.append(Token(value, "code", index))
            index += len(value)
    return result


def route_name(value: str) -> str | None:
    """Remove query values and normalize path parameters for comparison."""
    if not value.startswith("/") or value.startswith("//"):
        return None
    value = value.split("?", 1)[0].split("#", 1)[0]
    value = re.sub(r"\{[^{}]*\}", "{}", value)
    # Do not print static account IDs or token-shaped path segments.
    value = re.sub(
        r"(?<=/)[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?=/|$)",
        "{}",
        value,
    )
    value = re.sub(r"(?<=/)[A-Za-z0-9_.=-]{40,}(?=/|$)", "{}", value)
    if not re.fullmatch(r"/[A-Za-z0-9_./{}:@%=-]*", value):
        return None
    return value.rstrip("/") or "/"


def is_messenger_route(path: str) -> bool:
    """Accept messenger namespaces, not other APIs sharing the same JS files."""
    if any(part in {".", ".."} for part in path.split("/")):
        return False
    return bool(
        re.match(
            r"^/(?:api/v1/messenger|v1/chat-files|chats|presence|reactions|media/upload)(?:/|$)",
            path,
        )
        or path.startswith("/chats{}")
        or path == "/browser-file-session"
        or re.fullmatch(r"/users/[^/]+/messages(?:/.*)?", path)
    )


def extract(source: str, filename: str) -> list[dict[str, Any]]:
    """Find explicit HTTP calls and event cases; unresolved paths stay hypotheses."""
    stream = tokens(source)
    findings: list[dict[str, Any]] = []
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for index, token in enumerate(stream):
        if token.kind != "code":
            continue
        if token.value in {"(", "{", "["}:
            stack.append(index)
        elif token.value in {")", "}", "]"}:
            if (
                not stack
                or stream[stack[-1]].value
                != {")": "(", "}": "{", "]": "["}[token.value]
            ):
                raise ValueError("Несогласованные скобки JavaScript")
            pairs[stack.pop()] = index
    if stack:
        raise ValueError("Незавершённый блок JavaScript")

    def add(
        kind: str, name: str, offset: int, method: str | None, confidence: str
    ) -> None:
        findings.append(
            {
                "kind": kind,
                "name": name,
                "method": method,
                "confidence": confidence,
                "evidence": {"file": filename, "offset": offset},
            }
        )

    for index, token in enumerate(stream):
        if token.kind not in {"string", "template"}:
            continue
        path = route_name(token.value)
        if path is None or not is_messenger_route(path):
            continue
        # A literal first argument to .get/.post/... supplies a visible HTTP method.
        method = None
        if (
            index >= 3
            and stream[index - 1].value == "("
            and stream[index - 2].value.upper() in HTTP_METHODS
            and stream[index - 3].value == "."
        ):
            method = stream[index - 2].value.upper()
        # fetch(path, {method: "POST"}) / request("POST", path).
        if (
            index >= 3
            and stream[index - 1].value == ","
            and stream[index - 2].value in HTTP_METHODS
            and stream[index - 3].value == "("
        ):
            method = stream[index - 2].value
        if (
            index >= 2
            and stream[index - 1].value == "("
            and stream[index - 2].value == "fetch"
        ):
            end = pairs.get(index - 1, index)
            method = "GET" if end == index + 1 else None
            option_start = index + 2
            if option_start in pairs and stream[option_start].value == "{":
                pos, method = option_start + 1, "GET"
                while pos < pairs[option_start]:
                    if stream[pos].value == ".":
                        method = None  # A spread can supply/replace method.
                        break
                    if stream[pos].value == "method":
                        method = (
                            stream[pos + 2].value
                            if stream[pos + 1].value == ":"
                            and stream[pos + 2].kind == "string"
                            and stream[pos + 2].value in HTTP_METHODS
                            else None
                        )
                    pos = pairs[pos] + 1 if pos in pairs else pos + 1
        if method or path.startswith(("/api/", "/v1/", "/media/upload/")):
            confidence = (
                "confirmed"
                if method and "{}" not in path.replace("/{}", "")
                else "hypothesis"
            )
            add("route", path, token.offset, method, confidence)

    for index, token in enumerate(stream):
        if token.kind != "code" or token.value != "switch" or index + 1 not in pairs:
            continue
        condition_end = pairs[index + 1]
        condition = stream[index + 2 : condition_end]
        if len(condition) < 3 or [t.value for t in condition[-2:]] != [".", "type"]:
            continue
        body = condition_end + 1
        if body not in pairs or stream[body].value != "{":
            continue
        cases: list[Token] = []
        pos = body + 1
        while pos < pairs[body]:
            if (
                stream[pos].value == "case"
                and pos + 2 < len(stream)
                and stream[pos + 1].kind == "string"
                and stream[pos + 2].value == ":"
            ):
                cases.append(stream[pos + 1])
            if pos in pairs:
                pos = pairs[pos] + 1
            else:
                pos += 1
        if EVENT_ANCHORS.intersection(case.value for case in cases):
            for case in cases:
                if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", case.value):
                    add("event", case.value, case.offset, None, "confirmed")
    return findings


def _python_values(node: ast.AST, constants: dict[str, list[str]]) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _python_values(node.body, constants) + _python_values(
            node.orelse, constants
        )
    if isinstance(node, ast.Name):
        return constants.get(node.id, ["{}"])
    return ["{}"]


def sdk_routes(directory: Path) -> dict[str, set[str]]:
    """Inspect Python AST without importing the SDK or creating an HTTP client."""
    result: dict[str, set[str]] = {}
    if not directory.is_dir() or not (directory / "client.py").is_file():
        raise ValueError("Не найдены исходники SDK")
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            constants: dict[str, list[str]] = {}

            for assignment in ast.walk(function):
                if (
                    isinstance(assignment, ast.Assign)
                    and len(assignment.targets) == 1
                    and isinstance(assignment.targets[0], ast.Name)
                ):
                    constants[assignment.targets[0].id] = _python_values(
                        assignment.value, constants
                    )
            for call in ast.walk(function):
                if (
                    not isinstance(call, ast.Call)
                    or not isinstance(call.func, ast.Attribute)
                    or call.func.attr not in {"request", "api_request"}
                    or len(call.args) < 2
                ):
                    continue
                method, route = call.args[:2]
                if (
                    not isinstance(method, ast.Constant)
                    or method.value not in HTTP_METHODS
                ):
                    continue
                if not isinstance(route, (ast.Constant, ast.JoinedStr, ast.Name)):
                    continue
                variants = _python_values(route, constants)
                if isinstance(route, ast.JoinedStr):
                    parts = [
                        _python_values(part.value, constants)
                        if isinstance(part, ast.FormattedValue)
                        else _python_values(part, constants)
                        for part in route.values
                    ]
                    if any(len(part) > 16 for part in parts):
                        continue
                    count = 1
                    for part in parts:
                        count *= len(part)
                    if count > 64:
                        continue
                    variants = ["".join(parts) for parts in product(*parts)]
                for value in variants:
                    name = route_name(value)
                    if name and is_messenger_route(name):
                        for candidate in {
                            name,
                            re.sub(r"^/api/v1/messenger(?=/)", "", name),
                            re.sub(r"^/api(?=/)", "", name),
                        }:
                            if not is_messenger_route(candidate):
                                continue
                            owner = (
                                "Bot"
                                if path.stem == "client" or path.stem.startswith("_")
                                else path.stem
                            )
                            result.setdefault(f"{method.value} {candidate}", set()).add(
                                f"{owner}.{function.name}"
                            )
    return result


def snapshot(bundle: Path, sdk: Path) -> dict[str, Any]:
    files = sorted(bundle.rglob("*.js"))
    if not files:
        raise ValueError("В каталоге нет файлов .js")
    all_findings: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []
    for path in files:
        role = next(
            (name for name, pattern in ROLES.items() if path.match(pattern)), None
        )
        if role is None:
            continue
        relative = path.relative_to(bundle).as_posix()
        raw = path.read_bytes()
        all_findings.extend(extract(raw.decode("utf-8-sig"), relative))
        sources.append(
            {"file": relative, "role": role, "sha256": hashlib.sha256(raw).hexdigest()}
        )
    if not sources:
        raise ValueError("Не найдены файлы index-*, route-* или messengerMediaApi-*")
    if {source["role"] for source in sources} != set(ROLES):
        raise ValueError("Бандл неполон: нужны index-*, route-* и messengerMediaApi-*")
    routes = sdk_routes(sdk)
    event_methods: dict[str, set[str]] = {}
    dispatcher = ast.parse((sdk / "dispatcher.py").read_text(encoding="utf-8"))
    for function in ast.walk(dispatcher):
        if not isinstance(function, ast.FunctionDef):
            continue
        for call in ast.walk(function):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_event_decorator"
            ):
                for arg in call.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        event_methods.setdefault(arg.value, set()).add(
                            f"Dispatcher.{function.name}"
                        )
    for event in ("new_message", "new_messages"):
        if any(
            isinstance(node, ast.Constant) and node.value == event
            for node in ast.walk(dispatcher)
        ):
            event_methods.setdefault(event, set()).add("Dispatcher.handle")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in all_findings:
        key = finding["kind"], finding["name"]
        entry = entries.setdefault(
            key,
            {
                "kind": key[0],
                "name": key[1],
                "confidence": "hypothesis",
                "methods": [],
                "sdk_methods": [],
                "evidence": [],
            },
        )
        method = finding["method"]
        if method and method not in entry["methods"]:
            entry["methods"].append(method)
        if CONFIDENCE[finding["confidence"]] > CONFIDENCE[entry["confidence"]]:
            entry["confidence"] = finding["confidence"]
        entry["evidence"].append(
            {
                **finding["evidence"],
                "method": method,
                "confidence": finding["confidence"],
            }
        )
        if method:
            entry["sdk_methods"] = sorted(
                set(entry["sdk_methods"]) | routes.get(f"{method} {key[1]}", set())
            )
        elif key[0] == "event":
            entry["sdk_methods"] = sorted(event_methods.get(key[1], set()))
    for entry in entries.values():
        entry["methods"].sort()
    return {
        "schema_version": 1,
        "scope": "messenger",
        "sources": sources,
        "entries": [entries[key] for key in sorted(entries)],
    }


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    if before.get("schema_version") != 1 or after.get("schema_version") != 1:
        raise ValueError("Неизвестная версия описи")

    def records(data: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
        result = {}
        for entry in data["entries"]:
            if entry["confidence"] not in CONFIDENCE:
                raise ValueError("Неизвестный уровень уверенности")
            kind, name = entry["kind"], entry["name"]
            if kind not in {"route", "event"} or not isinstance(name, str):
                raise ValueError("Некорректная запись описи")
            if (kind == "route" and route_name(name) != name) or (
                kind == "event" and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
            ):
                raise ValueError("Некорректное имя записи")
            if not isinstance(entry["methods"], list) or any(
                method not in HTTP_METHODS for method in entry["methods"]
            ):
                raise ValueError("Некорректные HTTP-методы")
            if not isinstance(entry["sdk_methods"], list) or any(
                not isinstance(method, str)
                or not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", method)
                for method in entry["sdk_methods"]
            ):
                raise ValueError("Некорректные имена методов SDK")
            if kind == "route" and not is_messenger_route(name):
                continue
            key = f"{entry['kind']} {entry['name']}"
            if key in result:
                raise ValueError("Повтор записи в описи")
            result[key] = (
                sorted(entry["methods"]),
                sorted(entry["sdk_methods"]),
                entry["confidence"],
            )
        return result

    old, new = records(before), records(after)
    return {
        "new": sorted(new.keys() - old.keys()),
        "disappeared": sorted(old.keys() - new.keys()),
        "changed": sorted(
            key for key in old.keys() & new.keys() if old[key] != new[key]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Проверить API мессенджера в локальном бандле ИТД без запросов к серверу"
    )
    parser.add_argument("bundle", type=Path, help="Каталог распакованного бандла")
    parser.add_argument(
        "--inventory", type=Path, default=ROOT / "docs/api-coverage.json"
    )
    parser.add_argument("--sdk", type=Path, default=ROOT / "itd_bot")
    parser.add_argument(
        "--json", action="store_true", help="Печатать только JSON с расхождениями"
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Сохранить новую опись для ручной проверки; существующий файл не перезаписывается",
    )
    args = parser.parse_args(argv)
    try:
        before = json.loads(args.inventory.read_text(encoding="utf-8"))
        current = snapshot(args.bundle, args.sdk)
        changes = compare(before, current)
        if args.snapshot:
            with args.snapshot.open("x", encoding="utf-8") as output:
                output.write(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, SyntaxError):
        print(
            "Не удалось проверить бандл: проверьте файлы, формат JavaScript и опись. Данные исходников не выводятся.",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps(changes, ensure_ascii=False, indent=2))
    else:
        for key, title in [
            ("new", "Новое"),
            ("disappeared", "Исчезнувшее"),
            ("changed", "Изменившееся"),
        ]:
            print(f"{title}: {len(changes[key])}")
            for name in changes[key]:
                print(f"  {name}")
        print(
            "Статический разбор: поля ответов, динамические маршруты и доступность сервера не проверяются."
        )
    return int(any(changes.values()))


if __name__ == "__main__":
    raise SystemExit(main())
