from pathlib import Path

import pytest
import yaml

from chainsout.config import load_yaml


def test_env_config_overrides_files(tmp_path, monkeypatch):
    cfg = {"profile": {"company_reject": ["x"]}, "searches": {"a": 1}, "companies": {"companies": []}}
    monkeypatch.setenv("CHAINSOUT_CONFIG", yaml.safe_dump(cfg))
    assert load_yaml(tmp_path / "profile.yaml") == {"company_reject": ["x"]}
    assert load_yaml(tmp_path / "companies.yaml") == {"companies": []}


def test_env_config_missing_section(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAINSOUT_CONFIG", "profile: {}\n")
    with pytest.raises(SystemExit):
        load_yaml(tmp_path / "searches.yaml")


def test_files_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAINSOUT_CONFIG", raising=False)
    (tmp_path / "profile.yaml").write_text("a: 1\n")
    assert load_yaml(tmp_path / "profile.yaml") == {"a": 1}
