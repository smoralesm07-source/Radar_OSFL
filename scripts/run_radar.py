from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radar_osfl.engine import load_activity_taxonomy, load_config, utc_now, write_json


def main():
    sources = load_config(ROOT / "config" / "sources.yaml")["sources"]
    taxonomy = load_activity_taxonomy(ROOT / "config" / "osfl_activity_codes.csv")
    out = ROOT / "docs" / "data"
    out.mkdir(parents=True, exist_ok=True)
    source_catalog = [{
        "source_id": s["id"],
        "authority": s["authority"],
        "tier": s["tier"],
        "purpose": s["purpose"],
        "coverage": s.get("coverage"),
        "refresh": s.get("refresh"),
        "automated": s.get("automated"),
        "url": s.get("url") or s.get("official_page"),
    } for s in sources]
    summary = {
        "radar_id": "RADAR_OSFL",
        "version": "0.1.0",
        "generated_at": utc_now(),
        "status": "SOURCE_MODEL_READY",
        "sources_catalogued": len(source_catalog),
        "sii_activity_codes_screened": len(taxonomy),
        "historical_focus_from": 2020,
        "methodological_note": "La pertenencia al universo OSFL no implica riesgo. La priorización AML/CFT debe basarse en señales observables y evidencia trazable.",
    }
    write_json(source_catalog, out / "source_catalog.json")
    write_json(summary, out / "summary.json")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
