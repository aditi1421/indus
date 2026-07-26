import importlib
import json

import skills

# review finding: list_sources/describe_source/query_table/read_source all
# depend on ./manifest.json, which won't exist in production (EC2) -- they
# must not be permanently-broken tools polluting the agent's tool surface.
MANIFEST_TOOL_NAMES = {"list_sources", "describe_source", "query_table", "read_source"}


def test_manifest_tools_absent_when_manifest_missing():
    # No manifest.json in this repo checkout -> these four must not be
    # registered as callable tools.
    names = {s.name for s in skills.SKILLS}
    assert not (MANIFEST_TOOL_NAMES & names)


def test_manifest_tools_registered_when_manifest_present(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sources": []}))
    monkeypatch.setenv("NYAYA_MANIFEST", str(manifest))
    try:
        importlib.reload(skills)
        names = {s.name for s in skills.SKILLS}
        assert MANIFEST_TOOL_NAMES <= names
    finally:
        monkeypatch.delenv("NYAYA_MANIFEST", raising=False)
        importlib.reload(skills)  # restore module to its manifest-less state
