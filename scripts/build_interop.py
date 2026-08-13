from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/data"
RADAR_ID = "RADAR_OSFL"
VERSION = "1.0"


def clean(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def split_sources(value: object) -> list[str]:
    return sorted({part.strip() for part in clean(value).split("|") if part.strip()})


def evidence_id(entity_id: str, source_id: str, observed_at: str) -> str:
    raw = f"{RADAR_ID}|{entity_id}|{source_id}|{observed_at}".encode("utf-8")
    return "EVD-OSFL-" + hashlib.sha256(raw).hexdigest()[:24]


def build() -> dict:
    source = DATA / "osfl_universe_core.csv"
    if not source.exists():
        raise SystemExit("Falta docs/data/osfl_universe_core.csv")

    df = pd.read_csv(source, dtype=str).fillna("")
    if df.empty:
        raise RuntimeError("Universo OSFL núcleo vacío; no se publica cero falso")

    entities: list[dict] = []
    evidences: dict[str, dict] = {}
    rejected = 0

    for row in df.to_dict("records"):
        entity_id = clean(row.get("entity_id"))
        rut = clean(row.get("rut"))
        if not entity_id.startswith("ENT-RUT-") or not rut:
            rejected += 1
            continue

        observed_at = clean(row.get("generated_at"))
        sources = split_sources(row.get("evidence_sources")) or ["OSFL_CORE_UNIVERSE"]
        ids: list[str] = []
        for source_id in sources:
            eid = evidence_id(entity_id, source_id, observed_at)
            ids.append(eid)
            evidences[eid] = {
                "evidence_id": eid,
                "producer_id": RADAR_ID,
                "source_id": source_id,
                "ultimate_source_id": source_id,
                "source_url": None,
                "source_tier": "OFFICIAL" if source_id.startswith("sii_") else "PUBLIC",
                "capture_method": "RADAR_OSFL_GOVERNED_PIPELINE",
                "source_run_id": observed_at or None,
                "content_sha256": None,
                "quality_status": "VALID",
                "source_published_at": None,
                "retrieved_at": observed_at,
                "ingested_at": observed_at,
                "schema_version": VERSION,
            }

        status = clean(row.get("universe_status_refined")) or clean(row.get("universe_status"))
        entities.append({
            "source_entity_id": entity_id,
            "entity_id": entity_id,
            "entity_type": "OSFL",
            "canonical_name": clean(row.get("legal_name")) or None,
            "rut_normalized": rut,
            "aliases": [],
            "roles": ["OSFL"],
            "producer_ids": [RADAR_ID],
            "evidence_ids": ids,
            "identity_method": "RUT_EXACT",
            "identity_confidence": 1.0,
            "attributes": {
                "osfl_status": status or None,
                "osfl_confidence": clean(row.get("universe_confidence")) or None,
                "direct_confirmed": clean(row.get("direct_confirmed")).lower() == "true",
                "activity_codes": clean(row.get("activity_codes")) or None,
                "activity_names": clean(row.get("activity_names")) or None,
                "fatf_r8_screening": clean(row.get("fatf_r8_screening")) or None,
                "start_date": clean(row.get("start_date")) or None,
                "end_date": clean(row.get("end_date")) or None,
                "latest_region": clean(row.get("latest_region")) or None,
                "latest_main_activity": clean(row.get("latest_main_activity")) or None,
            },
            "schema_version": VERSION,
        })

    if not entities:
        raise RuntimeError("No existen entidades OSFL válidas para interoperabilidad")

    entity_path = DATA / "entity_hub_v1.jsonl"
    evidence_path = DATA / "evidence_v1.jsonl"
    with entity_path.open("w", encoding="utf-8") as handle:
        for row in entities:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with evidence_path.open("w", encoding="utf-8") as handle:
        for row in evidences.values():
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    catalog = {
        "interop_version": VERSION,
        "radar_id": RADAR_ID,
        "entity_hub_materialization": "docs/data/entity_hub_v1.jsonl",
        "global_entity_key": "ENT-RUT-{RUT_NORMALIZADO}",
        "unresolved_policy": "ENTITY_ID_NULL_CANDIDATE_ONLY",
        "exports": [
            {"path": "docs/data/entity_hub_v1.jsonl", "grain": "SOURCE_ENTITY", "primary_key": "source_entity_id", "canonical_type": "Entity"},
            {"path": "docs/data/evidence_v1.jsonl", "grain": "SOURCE_EVIDENCE", "primary_key": "evidence_id", "canonical_type": "Evidence"},
        ],
        "builder": "scripts/build_interop.py",
    }
    interop_status = {
        "interop_version": VERSION,
        "radar_id": RADAR_ID,
        "status": "ADAPTER_READY",
        "entities": len(entities),
        "evidence_records": len(evidences),
        "rejected_invalid_identity": rejected,
        "source_failure_is_zero": False,
    }
    (DATA / "interop_catalog_v1.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "interop_status_v1.json").write_text(json.dumps(interop_status, ensure_ascii=False, indent=2), encoding="utf-8")
    return interop_status


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))
