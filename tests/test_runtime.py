"""Tests for the MCP runtime info file helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jupyter_server_mcp import runtime


def _write_info(runtime_dir: Path, pid: int, overrides: dict | None = None) -> Path:
    """Write a minimal info file for ``pid`` in ``runtime_dir``."""
    path = runtime.info_file_path(runtime_dir, pid)
    payload = {
        "pid": pid,
        "host": "localhost",
        "port": 3001,
        "url": f"http://localhost:3001/mcp/{pid}",
        "name": "Jupyter MCP Server",
        "root_dir": str(runtime_dir),
    }
    if overrides:
        payload.update(overrides)
    runtime.write_info_file(path, payload)
    return path


def test_info_file_path_format(tmp_path):
    """Info files should follow the jpserver-mcp-<pid>.json pattern."""
    path = runtime.info_file_path(tmp_path, 4242)

    assert path.parent == tmp_path
    assert path.name == "jpserver-mcp-4242.json"


def test_write_info_file_is_atomic_and_readable(tmp_path):
    """Writing should round-trip the payload and not leave .tmp files behind."""
    path = tmp_path / "jpserver-mcp-1.json"
    payload = {"pid": 1, "port": 3001, "url": "http://localhost:3001/mcp"}

    runtime.write_info_file(path, payload)

    assert path.exists()
    assert json.loads(path.read_text()) == payload
    leftovers = [entry.name for entry in tmp_path.iterdir() if entry.suffix == ".tmp"]
    assert leftovers == []


def test_write_info_file_creates_parent_directory(tmp_path):
    """``write_info_file`` should create missing parent directories."""
    path = tmp_path / "nested" / "jpserver-mcp-9.json"

    runtime.write_info_file(path, {"pid": 9})

    assert path.exists()


def test_remove_info_file_missing_is_noop(tmp_path):
    """Removing a non-existent info file should not raise."""
    runtime.remove_info_file(tmp_path / "missing.json")


def test_list_running_mcp_servers_empty_dir(tmp_path):
    """An empty runtime directory yields no servers."""
    assert list(runtime.list_running_mcp_servers(tmp_path)) == []


def test_list_running_mcp_servers_missing_dir(tmp_path):
    """A missing runtime directory is handled gracefully."""
    missing = tmp_path / "does-not-exist"
    assert list(runtime.list_running_mcp_servers(missing)) == []


def test_list_running_mcp_servers_ignores_unrelated_files(tmp_path, monkeypatch):
    """Only ``jpserver-mcp-*.json`` files should be considered."""
    monkeypatch.setattr(runtime, "check_pid", lambda _pid: True)
    _write_info(tmp_path, 1)
    (tmp_path / "jpserver-42.json").write_text(json.dumps({"pid": 42}))
    (tmp_path / "random.json").write_text(json.dumps({"pid": 1}))

    servers = list(runtime.list_running_mcp_servers(tmp_path))

    assert [s["pid"] for s in servers] == [1]


def test_list_running_mcp_servers_skips_dead_and_cleans_up(tmp_path, monkeypatch):
    """Info files for dead PIDs should be skipped and deleted."""
    live_path = _write_info(tmp_path, 100)
    dead_path = _write_info(tmp_path, 999)

    monkeypatch.setattr(runtime, "check_pid", lambda pid: pid == 100)

    servers = list(runtime.list_running_mcp_servers(tmp_path))

    assert [s["pid"] for s in servers] == [100]
    assert live_path.exists()
    assert not dead_path.exists(), "stale info file should have been cleaned up"


def test_list_running_mcp_servers_skips_invalid_json(tmp_path, monkeypatch):
    """Malformed files should be skipped without crashing the iterator."""
    monkeypatch.setattr(runtime, "check_pid", lambda _pid: True)
    _write_info(tmp_path, 7)
    bad = tmp_path / "jpserver-mcp-99.json"
    bad.write_text("not json")

    servers = list(runtime.list_running_mcp_servers(tmp_path))

    assert [s["pid"] for s in servers] == [7]
    # Malformed files stay on disk so a later rewrite can succeed.
    assert bad.exists()


def test_list_running_mcp_servers_skips_missing_pid(tmp_path, monkeypatch):
    """Entries without a numeric pid should be dropped."""
    monkeypatch.setattr(runtime, "check_pid", lambda _pid: True)
    path = runtime.info_file_path(tmp_path, 1)
    runtime.write_info_file(path, {"url": "http://localhost:3001/mcp"})

    servers = list(runtime.list_running_mcp_servers(tmp_path))

    assert servers == []


@pytest.mark.parametrize("pid_value", ["nope", None, 1.5])
def test_list_running_mcp_servers_rejects_non_int_pid(tmp_path, monkeypatch, pid_value):
    """Only integer pids should be accepted."""
    monkeypatch.setattr(runtime, "check_pid", lambda _pid: True)
    path = runtime.info_file_path(tmp_path, 1)
    runtime.write_info_file(path, {"pid": pid_value})

    assert list(runtime.list_running_mcp_servers(tmp_path)) == []
