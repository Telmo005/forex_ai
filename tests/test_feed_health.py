"""
test_feed_health.py
======================
Prova que src/feed_health.py grava e lê o estado do feed corretamente,
e que degrada graciosamente (devolve None, nunca levanta exceção)
quando o ficheiro não existe ou está corrompido.
"""

from __future__ import annotations

from src.feed_health import read_feed_health, write_feed_health


def test_read_feed_health_missing_file_returns_none(tmp_path):
    path = str(tmp_path / "feed_health.json")
    assert read_feed_health(path) is None


def test_write_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "feed_health.json")
    write_feed_health("ok", consecutive_failures=0, path=path)
    result = read_feed_health(path)
    assert result["status"] == "ok"
    assert result["consecutive_failures"] == 0
    assert result["last_error"] is None
    assert "updated_at" in result


def test_write_degraded_status_with_error_message(tmp_path):
    path = str(tmp_path / "feed_health.json")
    write_feed_health("degraded", consecutive_failures=3, last_error="(-10004, 'No IPC connection')", path=path)
    result = read_feed_health(path)
    assert result["status"] == "degraded"
    assert result["consecutive_failures"] == 3
    assert "No IPC connection" in result["last_error"]


def test_read_feed_health_corrupted_file_returns_none(tmp_path):
    path = str(tmp_path / "feed_health.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ isto nao e json valido")
    assert read_feed_health(path) is None


def test_write_creates_parent_directories(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "feed_health.json")
    write_feed_health("ok", consecutive_failures=0, path=path)
    assert read_feed_health(path)["status"] == "ok"
