import json
from pathlib import Path

import pytest

from chainsout.models import Job

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--llm", action="store_true", default=False,
                     help="Ejecuta los tests que llaman al LLM (consumen cuota).")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--llm"):
        return
    skip = pytest.mark.skip(reason="necesita --llm")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


def load_jobs(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def job_from(d: dict) -> Job:
    fields = {k: v for k, v in d.items() if k in Job.__dataclass_fields__}
    return Job(**fields)


@pytest.fixture
def db(tmp_path):
    from chainsout.db import DB

    d = DB(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def profile():
    import yaml

    return yaml.safe_load((Path(__file__).parent.parent / "examples" / "embedded-rust" / "profile.yaml").read_text())
