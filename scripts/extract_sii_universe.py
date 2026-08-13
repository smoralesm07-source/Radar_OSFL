from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from radar_osfl.engine import download, extract_members, load_activity_taxonomy, load_config, normalize_sii_activity_chunk, read_chunks, utc_now, write_json


def main():
    cfg = load_config(ROOT / "config/sources.yaml")
    src = next(s for s in cfg["sources"] if s["id"] == "sii_activities_current")
    tax = load_activity_taxonomy(ROOT / "config/osfl_activity_codes.csv")
    run = ROOT / "data/runtime" / utc_now().replace(":", "-")
    raw = run / "sii_activities.zip"
    meta = download(src["url"], raw)
    files = extract_members(raw, run / "extract", src.get("member_globs"))
    parts = []
    for path in files:
        for chunk in read_chunks(path):
            out = normalize_sii_activity_chunk(chunk, tax)
            if not out.empty:
                parts.append(out[["entity_id", "rut", "activity_code", "activity_name", "osfl_signal_tier", "default_scope"]])
    if not parts:
        raise SystemExit("No se publican ceros: SII no produjo filas candidatas")
    data = pd.concat(parts, ignore_index=True).drop_duplicates()
    data = data[data["osfl_signal_tier"] != "EXCLUDE"]
    outdir = ROOT / "docs/data"; outdir.mkdir(parents=True, exist_ok=True)
    data.to_csv(outdir / "sii_osfl_activity_candidates.csv", index=False)
    counts = data.groupby(["activity_code", "activity_name"])["entity_id"].nunique().reset_index(name="entities")
    write_json({"generated_at": utc_now(), "candidate_entities": int(data.entity_id.nunique()), "candidate_rows": len(data), "by_activity": counts.to_dict("records"), "sha256": meta["sha256"], "warning": "Actividad SII es señal de cribado; no prueba por sí sola naturaleza jurídica OSFL."}, outdir / "sii_universe_summary.json")


if __name__ == "__main__":
    main()
