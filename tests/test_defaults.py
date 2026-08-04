import json
import yaml
from lorekeep.defaults import DEFAULT_SCHEMA, DEFAULT_CONFIG_YAML
from lorekeep.config import Config


def test_default_schema_is_valid_json_v4():
    d = DEFAULT_SCHEMA
    assert d["version"] == 4
    assert "service" in d["node_types"]
    assert "person" in d["node_types"]
    assert "domain" in d["node_types"]          # replaced concept
    assert "skill" in d["node_types"]           # work-context type
    assert "concept" not in d["node_types"]     # catch-all removed
    assert "tool" not in d["node_types"]
    assert "command" not in d["node_types"]
    assert "note" not in d["node_types"]
    assert "relates_to" in d["edge_types"]
    assert "contributes_to" in d["edge_types"]  # cross-ns bridge
    assert "mentions" not in d["edge_types"]    # weak catch-all removed
    assert d["common_node_props"] == {
        "summary": "string", "description": "string",
    }
    assert d["common_edge_props"] == {"description": "string"}
    assert d["node_types"]["person"]["plural"] == "People"
    assert d["node_types"]["decision"]["display_prop"] == "title"
    assert d["edge_types"]["depends_on"]["label"] == "Depends on"
    assert d["edge_types"]["depends_on"]["inverse_label"] == "Depended on by"
    json.dumps(d)  # serializable


def test_every_default_node_type_participates_in_an_edge():
    endpoints = set()
    for spec in DEFAULT_SCHEMA["edge_types"].values():
        for side in ("from", "to"):
            value = spec[side]
            endpoints.update([value] if isinstance(value, str) else value)
    assert set(DEFAULT_SCHEMA["node_types"]) - endpoints == set()


def test_default_config_yaml_loads_into_config():
    cfg = yaml.safe_load(DEFAULT_CONFIG_YAML)
    c = Config.model_validate(cfg)
    assert c.provider.model.startswith("openai/")
    assert c.install_source == "pypi"
    assert c.ns.default == ["me"]
    assert c.ns.personal_namespace == "me"


def test_default_config_yaml_has_no_backend():
    """backend is a removed dead field — must not appear in the template."""
    assert "backend" not in DEFAULT_CONFIG_YAML


def test_legacy_private_default_is_inferred_as_personal_namespace():
    config = Config.model_validate({"ns": {"default": ["private"]}})
    assert config.ns.personal_namespace == "private"
