import json
from pathlib import Path

import pytest

from hrdps_weather import hrdps

SAMPLE = Path(hrdps.__file__).parent / "data" / "sample_series.json"


@pytest.fixture(scope="session")
def data():
    """A real HRDPS run (generic Québec point) captured as JSON, so tests run offline."""
    return hrdps.Data(json.loads(SAMPLE.read_text(encoding="utf-8")))
