import sys
from pathlib import Path
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "textferry" / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def isolate_user_credentials(monkeypatch, tmp_path):
    """Tests use their own synthetic credentials, never the runner's account."""
    for name in ('PADDLEOCR_ACCESS_TOKEN', 'BAIDU_API_KEY', 'SILICONFLOW_API_KEY', 'TEXTFERRY_MOCK_OCR'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TEXTFERRY_CONFIG_DIR', str(tmp_path / 'isolated-config'))
