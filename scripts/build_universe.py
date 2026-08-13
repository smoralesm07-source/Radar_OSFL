from __future__ import annotations

from pathlib import Path
import json
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radar_osfl.engine import (
    download,
    extract_members,
    infer_scope,
    load_activity_taxonomy,
    load_config,
    normalize_company_year_chunk,
    normalize_sii_activity_chunk,
    normalize_sii_names_chunk,
    public_table_candidates,
    r8_screening_scope,
    read_chunks,
    source_status,
    utc_now,
    write_json,
)

TIER_RANK = {"EXCLUDE": 0, "MEDIUM": 1, "STRONG": 2, "VERY_STRONG": 3}
RANK_TIER = {v: k for k, v in TIER_RANK.items()}


def _source(cfg: dict, source_id: str) -> dict:
    return next(s for s in cfg["sources"] if s["id"] == source_id)


def _join_unique(values) -> str:
    vals = {str(v).strip() for v in values if str(v).strip() and str(v).strip().lower() != "nan"}
    return "|".join(sorted(vals))


def _download_extract(src: dict, run_dir: Path) -> tuple[list[Path], dict]:
    target = run_dir / f"{src['id']}.zip"
    meta = download(src["url"], target)
    files = extract_members(target, run_dir / src["id"], src.get("member_globs"))
    return files, meta


def collect_activity_candidates(cfg: dict, taxonomy: pd.DataFrame, run_dir: Path) -> tuple[pd.DataFrame, dict]:
    src = _source(cfg, "sii_activities_current")
    files, meta = _download_extract(src, run_dir)
    parts: list[pd.DataFrame] = []
    invalid_rut_rows = 0
    for path in files:
        for chunk in read_chunks(path):
            out = normalize_sii_activity_chunk(chunk, taxonomy)
            if out.empty:
                continue
            out = out[out["osfl_signal_tier"] != "EXCLUDE"].copy()
            invalid_rut_rows += int(out["entity_id"].isna().sum())
            out = out[out["entity_id"].notna()].copy()
            if not out.empty:
                parts.append(out[[
                    "entity_id", "rut", "activity_code", "activity_name",
                    "osfl_signal_tier", "default_scope"
                ]])
    if not parts:
        raise RuntimeError("SII actividades no produjo candidatos OSFL con RUT válido; no se publican ceros.")
    data = pd.concat(parts, ignore_index=True).drop_duplicates()
    meta["invalid_rut_rows"] = invalid_rut_rows
    return data, meta


def collect_direct_sii_confirmations(cfg: dict) -> tuple[pd.DataFrame, list[dict]]:
    frames: list[pd.DataFrame] = []
    statuses: list[dict] = []
    for source_id in ("sii_osfl_products", "sii_osfl_food_legacy"):
        src = _source(cfg, source_id)
        try:
            frame, meta = public_table_candidates(source_id, src["url"])
            valid = frame[frame["entity_id"].notna()].copy()
            if not valid.empty:
                frames.append(valid)
            statuses.append(source_status(source_id, True, rows=len(valid), meta=meta))
        except Exception as exc:
            statuses.append(source_status(source_id, False, error=f"{type(exc).__name__}: {exc}"))
    if not frames:
        return pd.DataFrame(columns=[
            "source_id", "rut", "entity_id", "legal_name", "legal_name_norm", "source_url", "retrieved_at"
        ]), statuses
    return pd.concat(frames, ignore_index=True).drop_duplicates(), statuses


def collect_names(cfg: dict, wanted_ruts: set[str], run_dir: Path) -> tuple[pd.DataFrame, dict]:
    src = _source(cfg, "sii_names_current")
    files, meta = _download_extract(src, run_dir)
    parts: list[pd.DataFrame] = []
    for path in files:
        for chunk in read_chunks(path):
            out = normalize_sii_names_chunk(chunk, wanted_ruts)
            if not out.empty:
                parts.append(out)
    if not parts:
        raise RuntimeError("SII nombres no logró enriquecer ninguna entidad del universo.")
    return pd.concat(parts, ignore_index=True).drop_duplicates(), meta


def collect_company_year(cfg: dict, wanted_ruts: set[str], run_dir: Path) -> tuple[pd.DataFrame, dict]:
    src = _source(cfg, "sii_company_year")
    files, meta = _download_extract(src, run_dir)
    parts: list[pd.DataFrame] = []
    for path in files:
        for chunk in read_chunks(path):
            out = normalize_company_year_chunk(chunk, wanted_ruts)
            if not out.empty:
                parts.append(out)
    if not parts:
        return pd.DataFrame(columns=[
            "entity_id", "rut", "commercial_year", "sales_band", "workers", "region",
            "main_activity", "taxpayer_type", "taxpayer_subtype"
        ]), meta
    data = pd.concat(parts, ignore_index=True).drop_duplicates()
    data["commercial_year_num"] = pd.to_numeric(data["commercial_year"], errors="coerce").astype("Int64")
    data = data[data["commercial_year_num"].between(2020, 2024, inclusive="both")].copy()
    return data, meta


def activity_entity_rollup(activity: pd.DataFrame) -> pd.DataFrame:
    x = activity.copy()
    x["tier_rank"] = x["osfl_signal_tier"].map(TIER_RANK).fillna(0).astype(int)
    base = x.groupby(["entity_id", "rut"], as_index=False).agg(
        activity_codes=("activity_code", _join_unique),
        activity_names=("activity_name", _join_unique),
        activity_scopes=("default_scope", _join_unique),
        max_tier_rank=("tier_rank", "max"),
        activity_count=("activity_code", "nunique"),
    )
    tiers = x.groupby("entity_id")["osfl_signal_tier"].apply(list).to_dict()
    scopes = x.groupby("entity_id")["default_scope"].apply(list).to_dict()
    base["max_activity_signal"] = base["max_tier_rank"].map(RANK_TIER)
    base["activity_universe_scope"] = base["entity_id"].map(lambda e: infer_scope(tiers.get(e, [])))
    base["fatf_r8_screening"] = base["entity_id"].map(lambda e: r8_screening_scope(scopes.get(e, [])))
    return base.drop(columns=["max_tier_rank"])


def direct_rollup(direct: pd.DataFrame) -> pd.DataFrame:
    if direct.empty:
        return pd.DataFrame(columns=["entity_id", "rut", "direct_sources", "direct_name"])
    names = direct.sort_values(["entity_id", "legal_name"]).groupby("entity_id")["legal_name"].first()
    out = direct.groupby(["entity_id", "rut"], as_index=False).agg(direct_sources=("source_id", _join_unique))
    out["direct_name"] = out["entity_id"].map(names).fillna("")
    return out


def history_rollup(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame(columns=["entity_id", "first_company_year", "last_company_year", "company_year_count", "years_present"])
    x = history[history["commercial_year_num"].notna()].copy()
    out = x.groupby("entity_id", as_index=False).agg(
        first_company_year=("commercial_year_num", "min"),
        last_company_year=("commercial_year_num", "max"),
        company_year_count=("commercial_year_num", "nunique"),
    )
    years = x.groupby("entity_id")["commercial_year_num"].apply(
        lambda s: "|".join(str(int(v)) for v in sorted(set(s.dropna().tolist())))
    )
    out["years_present"] = out["entity_id"].map(years).fillna("")
    for year in range(2020, 2025):
        present = set(x.loc[x["commercial_year_num"] == year, "entity_id"])
        out[f"company_presence_{year}"] = out["entity_id"].isin(present)
    latest = x.sort_values(["entity_id", "commercial_year_num"]).groupby("entity_id", as_index=False).tail(1)
    latest = latest[["entity_id", "sales_band", "workers", "region", "main_activity", "taxpayer_type", "taxpayer_subtype"]].rename(columns={
        "sales_band": "latest_sales_band",
        "workers": "latest_workers",
        "region": "latest_region",
        "main_activity": "latest_main_activity",
        "taxpayer_type": "latest_taxpayer_type",
        "taxpayer_subtype": "latest_taxpayer_subtype",
    })
    return out.merge(latest, on="entity_id", how="left")


def build_master(activity: pd.DataFrame, direct: pd.DataFrame, names: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    a = activity_entity_rollup(activity)
    d = direct_rollup(direct)
    keys = pd.concat([
        a[["entity_id", "rut"]],
        d[["entity_id", "rut"]] if not d.empty else pd.DataFrame(columns=["entity_id", "rut"]),
    ], ignore_index=True).drop_duplicates()
    master = keys.merge(a, on=["entity_id", "rut"], how="left").merge(d, on=["entity_id", "rut"], how="left")
    master = master.merge(names, on=["entity_id", "rut"], how="left")
    master = master.merge(history_rollup(history), on="entity_id", how="left")

    for col in ["direct_sources", "direct_name", "legal_name", "legal_name_norm", "start_date", "end_date",
                "activity_codes", "activity_names", "activity_scopes", "max_activity_signal", "activity_universe_scope",
                "fatf_r8_screening", "years_present"]:
        if col not in master:
            master[col] = ""
        master[col] = master[col].fillna("")

    master["legal_name"] = master["legal_name"].where(master["legal_name"].ne(""), master["direct_name"])
    master["direct_confirmed"] = master["direct_sources"].ne("")
    rank = master["max_activity_signal"].map(TIER_RANK).fillna(0).astype(int)
    master["universe_status"] = "UNRESOLVED"
    master.loc[rank.eq(1), "universe_status"] = "CANDIDATE_REVIEW_SII_ACTIVITY"
    master.loc[rank.ge(2), "universe_status"] = "CANDIDATE_STRONG_SII_ACTIVITY"
    master.loc[master["direct_confirmed"], "universe_status"] = "CONFIRMED_DIRECT_SII_REGISTRY"
    master["universe_confidence"] = master["universe_status"].map({
        "CONFIRMED_DIRECT_SII_REGISTRY": "CONFIRMED",
        "CANDIDATE_STRONG_SII_ACTIVITY": "HIGH_CANDIDATE",
        "CANDIDATE_REVIEW_SII_ACTIVITY": "REVIEW",
        "UNRESOLVED": "UNRESOLVED",
    })
    master["fatf_r8_screening"] = master["fatf_r8_screening"].replace("", "NOT_DETERMINED")
    master["current_tax_status"] = master["end_date"].map(lambda v: "NO_TERMINATION_REPORTED" if not str(v).strip() else "TERMINATION_REPORTED")

    history_ids = set(history["entity_id"]) if not history.empty else set()
    name_ids = set(names["entity_id"]) if not names.empty else set()
    activity_ids = set(activity["entity_id"])
    direct_map = direct.groupby("entity_id")["source_id"].apply(list).to_dict() if not direct.empty else {}

    def evidence_sources(eid: str) -> str:
        srcs: list[str] = []
        if eid in activity_ids:
            srcs.append("sii_activities_current")
        srcs.extend(direct_map.get(eid, []))
        if eid in name_ids:
            srcs.append("sii_names_current")
        if eid in history_ids:
            srcs.append("sii_company_year")
        return "|".join(sorted(set(srcs)))

    master["evidence_sources"] = master["entity_id"].map(evidence_sources)
    master["evidence_source_count"] = master["evidence_sources"].map(lambda s: len([x for x in s.split("|") if x]))
    master["generated_at"] = utc_now()

    order = [
        "entity_id", "rut", "legal_name", "universe_status", "universe_confidence", "direct_confirmed", "direct_sources",
        "activity_codes", "activity_names", "max_activity_signal", "activity_universe_scope", "fatf_r8_screening",
        "start_date", "end_date", "current_tax_status", "first_company_year", "last_company_year", "company_year_count",
        "years_present", "company_presence_2020", "company_presence_2021", "company_presence_2022", "company_presence_2023",
        "company_presence_2024", "latest_sales_band", "latest_workers", "latest_region", "latest_main_activity",
        "latest_taxpayer_type", "latest_taxpayer_subtype", "evidence_sources", "evidence_source_count", "generated_at"
    ]
    for col in order:
        if col not in master:
            master[col] = ""
    return master[order].sort_values(["universe_status", "legal_name", "rut"]).reset_index(drop=True)


def main() -> None:
    cfg = load_config(ROOT / "config/sources.yaml")
    taxonomy = load_activity_taxonomy(ROOT / "config/osfl_activity_codes.csv")
    run_dir = ROOT / "data/runtime" / utc_now().replace(":", "-")
    outdir = ROOT / "docs/data"
    export_dir = ROOT / "data/export"
    outdir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)

    statuses: list[dict] = []

    activity, activity_meta = collect_activity_candidates(cfg, taxonomy, run_dir)
    statuses.append(source_status("sii_activities_current", True, rows=len(activity), meta=activity_meta))

    direct, direct_statuses = collect_direct_sii_confirmations(cfg)
    statuses.extend(direct_statuses)

    wanted_ruts = set(activity["rut"].dropna()) | set(direct["rut"].dropna())
    if not wanted_ruts:
        raise RuntimeError("Universo base vacío: se aborta para no publicar un cero falso.")

    names, names_meta = collect_names(cfg, wanted_ruts, run_dir)
    statuses.append(source_status("sii_names_current", True, rows=len(names), meta=names_meta))

    history, history_meta = collect_company_year(cfg, wanted_ruts, run_dir)
    statuses.append(source_status("sii_company_year", True, rows=len(history), meta=history_meta))

    master = build_master(activity, direct, names, history)
    if master.empty:
        raise RuntimeError("La consolidación produjo universo vacío: se aborta.")

    # Salidas compactas en GitHub/Pages.
    master.to_csv(outdir / "osfl_universe.csv", index=False)
    activity.to_csv(outdir / "osfl_activity_evidence.csv", index=False)

    # Producto pesado y regenerable para consumo analítico/interoperabilidad.
    if not history.empty:
        history.to_parquet(export_dir / "osfl_entity_year.parquet", index=False)

    by_status = master.groupby("universe_status")["entity_id"].nunique().sort_values(ascending=False).to_dict()
    by_activity = activity.groupby(["activity_code", "activity_name"])["entity_id"].nunique().reset_index(name="entities")
    by_year = []
    if not history.empty:
        by_year = (
            history.groupby("commercial_year_num")["entity_id"].nunique().reset_index(name="entities")
            .dropna(subset=["commercial_year_num"])
            .sort_values("commercial_year_num")
            .assign(commercial_year=lambda d: d["commercial_year_num"].astype(int))
            [["commercial_year", "entities"]]
            .to_dict("records")
        )

    summary = {
        "radar_id": "RADAR_OSFL",
        "version": "0.2.0",
        "generated_at": utc_now(),
        "status": "UNIVERSE_MATERIALIZED",
        "universe_entities": int(master["entity_id"].nunique()),
        "confirmed_direct_sii_registry": int(master["direct_confirmed"].sum()),
        "fatf_r8_screening_candidates": int((master["fatf_r8_screening"] == "FATF_R8_CANDIDATE").sum()),
        "entities_with_company_year_2020_2024": int(master["company_year_count"].fillna(0).astype(float).gt(0).sum()),
        "by_universe_status": {str(k): int(v) for k, v in by_status.items()},
        "by_activity": by_activity.to_dict("records"),
        "company_presence_by_year": by_year,
        "sources": statuses,
        "quality": {
            "invalid_rut_rows_discarded_from_activity": int(activity_meta.get("invalid_rut_rows", 0)),
            "entity_id_rule": "ENT-RUT-{RUT_VALIDADO}",
            "source_failure_is_zero": False,
        },
        "interpretation": {
            "confirmed": "Entidad presente en una nómina SII que declara explícitamente instituciones sin fines de lucro.",
            "candidate": "Entidad con actividad económica SII asociativa/OSFL; requiere corroboración jurídica cuando no existe confirmación directa.",
            "company_year": "La presencia 2020-2024 indica que el RUT fue clasificado como empresa/persona jurídica por SII en ese año; no prueba por sí sola condición OSFL histórica.",
            "fatf_r8": "FATF_R8_CANDIDATE es solo cribado funcional para revisión; no equivale a riesgo FT ni a inclusión definitiva en el subconjunto de Recomendación 8.",
        },
    }
    write_json(summary, outdir / "universe_summary.json")
    write_json(statuses, outdir / "source_status.json")
    print(json.dumps({k: summary[k] for k in ["version", "status", "universe_entities", "confirmed_direct_sii_registry", "fatf_r8_screening_candidates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
