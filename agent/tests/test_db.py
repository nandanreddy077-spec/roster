import importlib

import db


def test_data_dir_defaults_to_local_agent_data_folder():
    importlib.reload(db)
    try:
        assert db.DATA_DIR.name == "data"
        assert db.DATA_DIR.parent.name == "agent"
    finally:
        importlib.reload(db)


def test_data_dir_configurable_via_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("ROSTER_DATA_DIR", str(tmp_path))
    importlib.reload(db)
    try:
        assert db.DATA_DIR == tmp_path
    finally:
        monkeypatch.delenv("ROSTER_DATA_DIR", raising=False)
        importlib.reload(db)
