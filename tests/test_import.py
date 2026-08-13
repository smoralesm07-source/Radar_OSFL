from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_package_imports():
    import radar_osfl
    assert radar_osfl.__version__ == "0.1.0"
