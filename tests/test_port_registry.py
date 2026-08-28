"""Tests for src/port_registry.py -- same coverage as the implementation
this was copy-pasted from (SuiteControl's src/port_registry.py), since
this is the audited original for this repo now."""
import socket

import pytest

from src import port_registry


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    fake_path = tmp_path / "ports.json"
    monkeypatch.setattr(port_registry, "REGISTRY_PATH", fake_path)
    return fake_path


def test_load_registry_missing_file_returns_empty(isolated_registry):
    assert port_registry.load_registry() == {}


def test_save_then_load_round_trips(isolated_registry):
    port_registry.save_registry({"app": {"app": {"port": 8800, "category": "internal"}}})
    assert port_registry.load_registry() == {"app": {"app": {"port": 8800, "category": "internal"}}}


def test_load_registry_corrupt_json_returns_empty(isolated_registry):
    isolated_registry.parent.mkdir(parents=True, exist_ok=True)
    isolated_registry.write_text("{not valid json", encoding="utf-8")
    assert port_registry.load_registry() == {}


def test_is_port_free_true_for_unbound_port():
    assert port_registry.is_port_free(58422) is True


def test_is_port_free_false_when_something_is_listening():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    port = s.getsockname()[1]
    try:
        assert port_registry.is_port_free(port) is False
        assert port_registry.is_port_up(port) is True
    finally:
        s.close()


def test_resolve_port_falls_back_to_default_when_no_prior(monkeypatch):
    monkeypatch.setattr(port_registry, "is_port_free", lambda port, host="127.0.0.1": True)
    service = {"key": "app", "default_port": 8800}
    assert port_registry.resolve_port("3d-printing-model-prompt", service, {}) == 8800


def test_resolve_port_skips_ports_claimed_by_other_projects(monkeypatch):
    service = {"key": "app", "default_port": 8800}
    reg = {"someone-else": {"svc": {"port": 8800, "category": "internal"}}}
    port = port_registry.resolve_port("3d-printing-model-prompt", service, reg)
    assert port != 8800
