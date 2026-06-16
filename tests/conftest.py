"""Shared pytest fixtures.

Adds the project root to sys.path and provides a mock .qvf (in the *real* Qlik
container format) plus its extracted app for integration tests.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.create_mock_qvf import create_mock_qvf  # noqa: E402
from extraction.qvf_reader import QVFReader  # noqa: E402
from extraction.extractor import Extractor  # noqa: E402


@pytest.fixture
def mock_qvf(tmp_path) -> Path:
    return create_mock_qvf(str(tmp_path / "test_app.qvf"))


@pytest.fixture
def raw_app(mock_qvf):
    return QVFReader(mock_qvf).read()


@pytest.fixture
def extracted_app(raw_app):
    return Extractor(raw_app).extract()
