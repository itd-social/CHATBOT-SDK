"""Bundle inventory checks use small fictional JavaScript fragments only."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_bundle import (
    compare,
    extract,
    main,
    route_name,
    sdk_routes,
    snapshot,
    tokens,
)


@pytest.fixture
def sources(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index-test.js").write_text(
        'api.get("/chats"); switch(e.type){case "new_message": break; case "typing": break;}',
        encoding="utf-8",
    )
    (bundle / "route-test.js").write_text('navigate("/chats/new");', encoding="utf-8")
    (bundle / "messengerMediaApi-test.js").write_text(
        'api.post("/media/upload/init", {});', encoding="utf-8"
    )
    sdk = tmp_path / "itd_bot"
    sdk.mkdir()
    (sdk / "client.py").write_text(
        'class Bot:\n def get_chats(self):\n  return self.request("GET", "/chats")\n',
        encoding="utf-8",
    )
    (sdk / "dispatcher.py").write_text(
        'def message(self):\n return "new_message"\ndef reaction(self):\n return self._event_decorator("message_reactions")\n',
        encoding="utf-8",
    )
    return bundle, sdk


def test_routes_methods_templates_escapes_and_queries():
    data = extract(
        r"""api.get(`/chats/${escape(id)}/messages?token=private`);
api.post('/chats/dm'); api.patch("\x2fchats/\u007b\u007d/mute");
fetch('/api/widgets', {method: 'DELETE'}); fetch('/api/users');
request('PUT', '/api/settings');""",
        "index-test.js",
    )
    assert {(d["name"], d["method"]) for d in data} == {
        ("/chats/{}/messages", "GET"),
        ("/chats/dm", "POST"),
        ("/chats/{}/mute", "PATCH"),
        ("/api/widgets", "DELETE"),
        ("/api/users", "GET"),
        ("/api/settings", "PUT"),
    }
    assert "private" not in json.dumps(data)


def test_comments_regex_navigation_and_arbitrary_switches_are_not_api():
    source = r"""// api.get("/api/fake")
/* switch(e.type){case "new_message": break;} */
const pattern = /api.get\("\/api\/fake"\)/;
navigate("/chats/new");
switch(view.type){case "photo": break;}
switch(event.type){case "new_message": nested(); break;
case "future_event": switch(x.type){case "inner_only": break;} break;}
"""
    assert {(d["kind"], d["name"]) for d in extract(source, "index-test.js")} == {
        ("event", "new_message"),
        ("event", "future_event"),
    }


def test_nested_template_expressions_and_escaped_backticks():
    source = "api.get(`/chats/${lookup({key: `a${id}`})}/messages`); const x = `a\\`b`;"
    assert extract(source, "x.js")[0]["name"] == "/chats/{}/messages"


def test_unresolved_paths_remain_hypotheses():
    data = extract(
        'const endpoint="/api/future"; api.get(`/chats${query}`);', "index-test.js"
    )
    assert all(d["confidence"] == "hypothesis" for d in data)
    assert {d["name"] for d in data} == {"/api/future", "/chats{}"}


def test_fetch_options_only_consider_top_level_method_and_no_dynamic_spreads():
    source = """fetch("/api/one", {headers: {method: "DELETE"}});
fetch("/api/two", {method: operation});
fetch("/api/three", {method: "POST", ...options});
fetch("/api/four", options);"""
    data = {item["name"]: item for item in extract(source, "index-test.js")}
    assert data["/api/one"]["method"] == "GET"
    for path in ["/api/two", "/api/three", "/api/four"]:
        assert data[path]["method"] is None
        assert data[path]["confidence"] == "hypothesis"


@pytest.mark.parametrize(
    "source",
    [
        '"unfinished',
        "/* unfinished",
        "`x${foo",
        "const x=/unterminated",
        "const x=/a\nb/",
        "foo({]",
        "foo(",
    ],
)
def test_malformed_javascript_fails_explicitly(source):
    with pytest.raises(ValueError):
        tokens(source) if source[0] in '"/`' or "const" in source else extract(
            source, "x.js"
        )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("https://example.test/api/users", None),
        ("//example.test/path", None),
        ("/api/users?token=private", "/api/users"),
        ("/chats/01234567-89ab-cdef-0123-456789abcdef", "/chats/{}"),
        ("/api/" + "x" * 50, "/api/{}"),
        ("/api/\nprivate", None),
        ("/chats/{chat_id}/", "/chats/{}"),
    ],
)
def test_route_names_strip_private_values_and_reject_nonpaths(path, expected):
    assert route_name(path) == expected


def test_snapshot_hashes_roles_and_sdk_coverage(sources):
    bundle, sdk = sources
    data = snapshot(bundle, sdk)
    assert {s["role"] for s in data["sources"]} == {"index", "route", "media"}
    assert all(
        len(s["sha256"]) == 64 and not Path(s["file"]).is_absolute()
        for s in data["sources"]
    )
    chat = next(e for e in data["entries"] if e["name"] == "/chats")
    assert chat["sdk_methods"] == ["Bot.get_chats"]
    event = next(e for e in data["entries"] if e["name"] == "new_message")
    assert event["sdk_methods"] == ["Dispatcher.handle"]
    assert compare(data, snapshot(bundle, sdk)) == {
        "new": [],
        "disappeared": [],
        "changed": [],
    }


def test_sdk_static_analysis_handles_scope_variants_and_does_not_execute(sources):
    _, sdk = sources
    (sdk / "_files.py").write_text(
        'raise RuntimeError("must not execute")\ndef send(self, all):\n scope="all" if all else "me"\n return self.request("POST", f"/chats/{chat}/delete-for-{scope}")\n',
        encoding="utf-8",
    )
    (sdk / "profile.py").write_text(
        'def user(self):\n return self.api_request("GET", "/api/users/me")\n',
        encoding="utf-8",
    )
    routes = sdk_routes(sdk)
    assert routes["POST /chats/{}/delete-for-all"] == {"Bot.send"}
    assert routes["POST /chats/{}/delete-for-me"] == {"Bot.send"}
    assert routes["GET /users/me"] == {"profile.user"}


def test_compare_detects_routes_events_http_method_and_sdk_changes(sources):
    bundle, sdk = sources
    before = snapshot(bundle, sdk)
    (bundle / "index-test.js").write_text(
        'api.post("/chats"); api.get("/api/future"); switch(e.type){case "new_message": break; case "future_event": break;}',
        encoding="utf-8",
    )
    changes = compare(before, snapshot(bundle, sdk))
    assert changes == {
        "new": ["event future_event", "route /api/future"],
        "disappeared": ["event typing"],
        "changed": ["route /chats"],
    }
    (sdk / "client.py").write_text(
        'def renamed(self):\n return self.request("POST", "/chats")\n', encoding="utf-8"
    )
    assert "route /chats" in compare(before, snapshot(bundle, sdk))["changed"]


def test_file_hash_and_offset_changes_do_not_claim_api_drift(sources):
    bundle, sdk = sources
    before = snapshot(bundle, sdk)
    file = bundle / "index-test.js"
    file.rename(bundle / "index-renamed.js")
    (bundle / "index-renamed.js").write_text(
        "/* comment */" + file.with_name("index-renamed.js").read_text(),
        encoding="utf-8",
    )
    assert not any(compare(before, snapshot(bundle, sdk)).values())


@pytest.mark.parametrize(
    "mutation", ["version", "confidence", "duplicate", "kind", "name", "method", "sdk"]
)
def test_invalid_inventory_is_rejected(sources, mutation):
    bundle, sdk = sources
    data = snapshot(bundle, sdk)
    bad = copy.deepcopy(data)
    if mutation == "version":
        bad["schema_version"] = 99
    elif mutation == "duplicate":
        bad["entries"].append(copy.deepcopy(bad["entries"][0]))
    else:
        key, value = {
            "confidence": ("confidence", "sure"),
            "kind": ("kind", "whatever"),
            "name": ("name", "\nprivate"),
            "method": ("methods", ["INVALID"]),
            "sdk": ("sdk_methods", ["\nprivate"]),
        }[mutation]
        bad["entries"][0][key] = value
    with pytest.raises(ValueError):
        compare(bad, data)


def test_cli_exit_codes_snapshot_and_no_secret_errors(sources, tmp_path, capsys):
    bundle, sdk = sources
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(snapshot(bundle, sdk)), encoding="utf-8")
    args = [str(bundle), "--sdk", str(sdk), "--inventory", str(inventory)]
    target = tmp_path / "candidate.json"
    assert main([*args, "--json", "--snapshot", str(target)]) == 0
    assert not any(json.loads(capsys.readouterr().out).values())
    assert target.is_file()
    assert main([*args, "--snapshot", str(target)]) == 2
    assert "private" not in capsys.readouterr().err
    (bundle / "index-test.js").write_text('api.get("/api/new");', encoding="utf-8")
    assert main(args) == 1
    assert "Новое:" in capsys.readouterr().out
    inventory.write_text("invalid private source", encoding="utf-8")
    assert main(args) == 2
    output = capsys.readouterr()
    assert output.out == "" and "private" not in output.err


def test_cli_subprocess_works_outside_repository(sources, tmp_path):
    bundle, sdk = sources
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(snapshot(bundle, sdk)), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tools/check_bundle.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            str(bundle),
            "--sdk",
            str(sdk),
            "--inventory",
            str(inventory),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0
    assert not any(json.loads(proc.stdout).values())


def test_missing_or_partial_inputs_are_not_reported_as_empty_success(sources, tmp_path):
    bundle, sdk = sources
    with pytest.raises(ValueError):
        snapshot(tmp_path / "missing", sdk)
    with pytest.raises(ValueError):
        sdk_routes(tmp_path / "missing")
    (bundle / "messengerMediaApi-test.js").unlink()
    with pytest.raises(ValueError):
        snapshot(bundle, sdk)
    for file in bundle.glob("*.js"):
        file.unlink()
    (bundle / "other.js").write_text("const x=1;", encoding="utf-8")
    with pytest.raises(ValueError):
        snapshot(bundle, sdk)


def test_repository_inventory_is_valid_and_records_inactive_features():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "docs/api-coverage.json").read_text(encoding="utf-8"))
    assert not any(compare(data, data).values())
    assert {item["role"] for item in data["sources"]} == {"index", "route", "media"}
    sticker = next(
        feature for feature in data["inactive_features"] if feature["name"] == "sticker"
    )
    assert sticker["confidence"] == "prepared_inactive"
    assert sticker["issue"] == 17
