import json
import yaml
from lorekeep.defaults import DEFAULT_SCHEMA, DEFAULT_CONFIG_YAML
from lorekeep.config import Config


def test_default_schema_is_valid_json_v2():
    d = DEFAULT_SCHEMA
    assert d["version"] == 2
    assert "service" in d["node_types"]
    assert "concept" in d["node_types"]
    assert "relates_to" in d["edge_types"]
    json.dumps(d)  # serializable


def test_default_config_yaml_loads_into_config():
    cfg = yaml.safe_load(DEFAULT_CONFIG_YAML)
    c = Config.model_validate(cfg)
    assert c.provider.model.startswith("openai/")
    assert c.install_source == "pypi"
    assert c.ns.default == ["public"]


def test_default_config_yaml_has_no_backend():
    """backend is a removed dead field — must not appear in the template."""
    assert "backend" not in DEFAULT_CONFIG_YAML
