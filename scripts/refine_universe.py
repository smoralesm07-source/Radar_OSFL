from __future__ import annotations

from pathlib import Path
import json
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/data"

COMMERCIAL_FORM = re.compile(
    r"(?:\bSPA\b|\bS\.?P\.?A\.?\b|SOCIEDAD POR ACCIONES|\bEIRL\b|EMPRESA INDIVIDUAL|"
    r"\bLTDA\b|\bLIMITADA\b|SOCIEDAD ANONIMA|\bS\.?A\.?\b)",
    re.IGNORECASE,
)

OSFL_NAME_EVIDENCE = re.compile(
    r"(?:FUNDACI[ÓO]N|CORPORACI[ÓO]N|ASOCIACI[ÓO]N|ORGANIZACI[ÓO]N NO GUBERNAMENTAL|\bONG\b|"
    r"IGLESIA|CONGREGACI[ÓO]N|PARROQUIA|ARZOBISPADO|OBISPADO|SINDICATO|FEDERACI[ÓO]N|"
    r"CONFEDERACI[ÓO]N|\bCLUB\b|CENTRO DE MADRES|CENTRO CULTURAL|CENTRO SOCIAL|CENTRO DE PADRES|"
    r"JUNTA DE VECINOS|UNI[ÓO]N COMUNAL|AGRUPACI[ÓO]N|COLEGIO PROFESIONAL|C[ÁA]MARA|"
    r"COMIT[ÉE]|COMUNIDAD IND[ÍI]GENA|CUERPO DE BOMBEROS|BOMBEROS|SOCIEDAD DE BENEFICENCIA|"
    r"HOGAR DE|ARZOBISPADO|VICAR[ÍI]A|CAPILLA|TEMPLO)",
    re.IGNORECASE,
)


def clean(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def main() -> None:
    source = DATA / "osfl_universe.csv"
    activity_path = DATA / "osfl_activity_evidence.csv"
    if not source.exists():
        raise SystemExit("Falta osfl_universe.csv; ejecute build_universe.py primero")

    df = pd.read_csv(source, dtype=str).fillna("")
    direct = df["direct_confirmed"].str.lower().eq("true")
    commercial = df["legal_name"].map(lambda x: bool(COMMERCIAL_FORM.search(clean(x))))
    positive_name = df["legal_name"].map(lambda x: bool(OSFL_NAME_EVIDENCE.search(clean(x))))
    signal = df["max_activity_signal"].str.upper()

    df["commercial_form_signal"] = commercial
    df["osfl_name_evidence"] = positive_name
    df["universe_in_scope"] = False
    df["universe_status_refined"] = "REVIEW_OUTSIDE_CORE"
    df["exclusion_reason"] = ""

    # Una nómina oficial que declara explícitamente OSFL prevalece sobre el nombre aparente.
    df.loc[direct, "universe_in_scope"] = True
    df.loc[direct, "universe_status_refined"] = "CONFIRMED_DIRECT_SII_REGISTRY"

    # Señales fuertes/muy fuertes: se incorporan salvo forma societaria comercial explícita.
    strong = signal.isin(["STRONG", "VERY_STRONG"]) & ~direct
    df.loc[strong & ~commercial, "universe_in_scope"] = True
    df.loc[strong & ~commercial, "universe_status_refined"] = "CORE_STRONG_SII_ACTIVITY"
    df.loc[strong & commercial, "exclusion_reason"] = "COMMERCIAL_LEGAL_FORM_CONFLICTS_WITH_OSFL_SCREEN"

    # 941100/941200 son señal media: solo ingresan al núcleo si el nombre también es compatible con OSFL.
    medium = signal.eq("MEDIUM") & ~direct
    df.loc[medium & ~commercial & positive_name, "universe_in_scope"] = True
    df.loc[medium & ~commercial & positive_name, "universe_status_refined"] = "CORE_MEDIUM_ACTIVITY_PLUS_NAME"
    df.loc[medium & commercial, "exclusion_reason"] = "COMMERCIAL_LEGAL_FORM_WITH_MEDIUM_ACTIVITY"
    df.loc[medium & ~commercial & ~positive_name, "exclusion_reason"] = "MEDIUM_ACTIVITY_WITHOUT_OSFL_NAME_EVIDENCE"

    core = df[df["universe_in_scope"]].copy()
    review = df[~df["universe_in_scope"]].copy()
    if core.empty:
        raise RuntimeError("La depuración produjo universo núcleo vacío; no se publica cero.")

    core.to_csv(DATA / "osfl_universe_core.csv", index=False)
    review.to_csv(DATA / "osfl_universe_review.csv", index=False)

    by_status = core.groupby("universe_status_refined")["entity_id"].nunique().sort_values(ascending=False)
    review_reasons = review.groupby("exclusion_reason")["entity_id"].nunique().sort_values(ascending=False)

    activity_summary = []
    if activity_path.exists():
        ev = pd.read_csv(activity_path, dtype=str).fillna("")
        ev = ev[ev["entity_id"].isin(set(core["entity_id"]))]
        activity_summary = (
            ev.groupby(["activity_code", "activity_name"])["entity_id"].nunique()
            .reset_index(name="entities").sort_values("entities", ascending=False).to_dict("records")
        )

    summary = {
        "version": "0.2.1",
        "status": "CORE_UNIVERSE_REFINED",
        "raw_universe_entities": int(df["entity_id"].nunique()),
        "core_universe_entities": int(core["entity_id"].nunique()),
        "review_excluded_entities": int(review["entity_id"].nunique()),
        "direct_confirmed_entities": int(direct.sum()),
        "commercial_form_conflicts": int((commercial & ~direct).sum()),
        "by_core_status": {str(k): int(v) for k, v in by_status.items()},
        "review_reasons": {str(k): int(v) for k, v in review_reasons.items()},
        "by_activity_core": activity_summary,
        "methodology": {
            "direct_registry": "Siempre integra el núcleo cuando el RUT está en una nómina SII que declara explícitamente instituciones sin fines de lucro.",
            "strong_activity": "Integra el núcleo si la actividad es STRONG/VERY_STRONG y no existe forma societaria comercial explícita en la razón social.",
            "medium_activity": "941100/941200 requieren además evidencia nominal compatible con asociación/gremio/colegio/cámara u otra forma OSFL y ausencia de forma societaria comercial.",
            "auditability": "Los descartados se conservan en osfl_universe_review.csv; no se eliminan de la evidencia bruta.",
        },
    }
    (DATA / "universe_refined_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "raw": summary["raw_universe_entities"],
        "core": summary["core_universe_entities"],
        "review": summary["review_excluded_entities"],
        "commercial_conflicts": summary["commercial_form_conflicts"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
