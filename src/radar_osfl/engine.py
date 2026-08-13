from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
import yaml

USER_AGENT = "Radar-OSFL/0.2 (+public OSINT; Chile)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (tuple, list)):
        parts = [clean_text(v) for v in value]
        return " ".join(p for p in parts if p and not p.lower().startswith("unnamed"))
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def slug(value: object) -> str:
    text = clean_text(value).upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", "_", text).strip("_").lower() or "unnamed"


def normalize_name(value: object) -> str:
    text = clean_text(value).upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def rut_dv(body: str) -> str:
    total = 0
    factor = 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    result = 11 - (total % 11)
    return "0" if result == 11 else "K" if result == 10 else str(result)


def normalize_rut(value: object, dv: object | None = None) -> str | None:
    if dv is None:
        raw = re.sub(r"[^0-9Kk]", "", clean_text(value))
        if len(raw) < 2:
            return None
        body, check = raw[:-1], raw[-1].upper()
    else:
        body = re.sub(r"[^0-9]", "", clean_text(value)).lstrip("0")
        check = re.sub(r"[^0-9Kk]", "", clean_text(dv)).upper()[:1]
    body = body.lstrip("0")
    if not body or not check or not body.isdigit():
        return None
    if rut_dv(body) != check:
        return None
    return f"{body}-{check}"


def entity_id(rut: str | None) -> str | None:
    return f"ENT-RUT-{rut}" if rut else None


def detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:200_000]
    for enc in ("utf-8-sig", "cp1252", "latin1"):
        try:
            sample.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    return "latin1"


def detect_separator(path: Path, encoding: str) -> str:
    sample = path.read_bytes()[:100_000].decode(encoding, errors="replace")
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t;|,").delimiter
    except csv.Error:
        counts = {sep: sample.count(sep) for sep in ("\t", ";", "|", ",")}
        return max(counts, key=counts.get)


def read_chunks(path: Path, chunksize: int = 150_000) -> Iterable[pd.DataFrame]:
    enc = detect_encoding(path)
    sep = detect_separator(path, enc)
    yield from pd.read_csv(path, sep=sep, encoding=enc, dtype=str, chunksize=chunksize,
                           low_memory=False, on_bad_lines="skip")


def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    seen: dict[str, int] = {}
    names: list[str] = []
    for col in out.columns:
        base = slug(col)
        # Algunos registros HTML repiten el mismo encabezado en MultiIndex.
        tokens = base.split("_")
        if len(tokens) % 2 == 0 and tokens[:len(tokens)//2] == tokens[len(tokens)//2:]:
            base = "_".join(tokens[:len(tokens)//2])
        count = seen.get(base, 0)
        seen[base] = count + 1
        names.append(base if count == 0 else f"{base}_{count + 1}")
    out.columns = names
    return out


def find_col(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    cols = set(columns)
    for alias in aliases:
        if alias in cols:
            return alias
    return None


def rut_series(df: pd.DataFrame) -> pd.Series:
    df = canonicalize_columns(df)
    rcol = find_col(df.columns, ["rut", "r_u_t", "rut_contribuyente", "rut_empresa", "numero_rut", "rut_numero"])
    dcol = find_col(df.columns, ["dv", "d_v", "digito_verificador", "dv_rut"])
    if rcol and dcol:
        return pd.Series([normalize_rut(r, d) for r, d in zip(df[rcol], df[dcol])], index=df.index)
    if rcol:
        return df[rcol].map(normalize_rut)
    return pd.Series([None] * len(df), index=df.index, dtype="object")


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_activity_taxonomy(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def download(url: str, target: Path, timeout: int = 300) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    size = 0
    with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        with target.open("wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                sha.update(chunk)
                size += len(chunk)
        return {
            "url": r.url,
            "path": str(target),
            "sha256": sha.hexdigest(),
            "bytes": size,
            "retrieved_at": utc_now(),
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
            "content_type": r.headers.get("Content-Type"),
        }


def extract_members(path: Path, out_dir: Path, patterns: list[str] | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not zipfile.is_zipfile(path):
        out = out_dir / path.name
        shutil.copy2(path, out)
        return [out]
    outputs: list[Path] = []
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if patterns:
            infos = [i for i in infos if any(fnmatch.fnmatch(Path(i.filename).name, p) for p in patterns)]
        else:
            infos = [i for i in infos if Path(i.filename).suffix.lower() in {".txt", ".csv", ".tsv"}]
        if not infos:
            raise ValueError(f"Sin miembros compatibles en {path}")
        for info in infos:
            out = out_dir / Path(info.filename).name
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            outputs.append(out)
    return outputs


def html_tables(url: str, timeout: int = 90) -> tuple[list[pd.DataFrame], dict]:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    meta = {
        "url": r.url,
        "retrieved_at": utc_now(),
        "sha256": hashlib.sha256(r.content).hexdigest(),
        "bytes": len(r.content),
        "content_type": r.headers.get("Content-Type"),
    }
    return tables, meta


def normalize_sii_activity_chunk(df: pd.DataFrame, taxonomy: pd.DataFrame) -> pd.DataFrame:
    x = canonicalize_columns(df)
    code_col = find_col(x.columns, ["codigo_actividad_economica", "codigo_actividad", "cod_actividad", "codigo"])
    if not code_col:
        return pd.DataFrame()
    wanted = set(taxonomy["activity_code"].astype(str))
    mask = x[code_col].astype(str).str.extract(r"(\d{6})", expand=False).isin(wanted)
    x = x.loc[mask].copy()
    if x.empty:
        return x
    x["rut"] = rut_series(x)
    x["entity_id"] = x["rut"].map(entity_id)
    x["activity_code"] = x[code_col].astype(str).str.extract(r"(\d{6})", expand=False)
    x = x.merge(taxonomy, on="activity_code", how="left")
    return x


def normalize_sii_names_chunk(df: pd.DataFrame, wanted_ruts: set[str]) -> pd.DataFrame:
    x = canonicalize_columns(df)
    x["rut"] = rut_series(x)
    x = x[x["rut"].isin(wanted_ruts)].copy()
    if x.empty:
        return x
    name_col = find_col(x.columns, ["razon_social", "nombre_razon_social", "nombre"])
    start_col = find_col(x.columns, ["fecha_inicio_actividades", "fecha_inicio", "inicio_actividades"])
    end_col = find_col(x.columns, ["fecha_termino_giro", "fecha_termino", "termino_giro"])
    x["entity_id"] = x["rut"].map(entity_id)
    x["legal_name"] = x[name_col].map(clean_text) if name_col else ""
    x["legal_name_norm"] = x["legal_name"].map(normalize_name)
    x["start_date"] = x[start_col].map(clean_text) if start_col else ""
    x["end_date"] = x[end_col].map(clean_text) if end_col else ""
    return x[["entity_id", "rut", "legal_name", "legal_name_norm", "start_date", "end_date"]].drop_duplicates()


def normalize_company_year_chunk(df: pd.DataFrame, wanted_ruts: set[str]) -> pd.DataFrame:
    x = canonicalize_columns(df)
    x["rut"] = rut_series(x)
    x = x[x["rut"].isin(wanted_ruts)].copy()
    if x.empty:
        return x
    x["entity_id"] = x["rut"].map(entity_id)
    aliases = {
        "commercial_year": ["ano_comercial", "anio_comercial", "ano", "year"],
        "sales_band": ["tramo_segun_ventas", "tramo_ventas", "tramo_venta"],
        "workers": ["numero_de_trabajadores_dependientes", "numero_trabajadores_dependientes", "nro_trabajadores", "trabajadores"],
        "region": ["region", "region_empresa"],
        "main_activity": ["actividad_economica_principal", "actividad_principal", "codigo_actividad_economica_principal"],
        "taxpayer_type": ["tipo_contribuyente"],
        "taxpayer_subtype": ["subtipo_contribuyente"],
    }
    for out_col, options in aliases.items():
        col = find_col(x.columns, options)
        x[out_col] = x[col].map(clean_text) if col else ""
    keep = ["entity_id", "rut"] + list(aliases)
    return x[keep].drop_duplicates()


def infer_scope(activity_tiers: Iterable[str]) -> str:
    tiers = set(activity_tiers)
    if "VERY_STRONG" in tiers or "STRONG" in tiers:
        return "OSFL_CANDIDATE"
    if "MEDIUM" in tiers:
        return "OSFL_REVIEW"
    return "UNRESOLVED"


def r8_screening_scope(default_scopes: Iterable[str]) -> str:
    return "FATF_R8_CANDIDATE" if "FATF_R8_CANDIDATE" in set(default_scopes) else "NOT_DETERMINED"


def source_status(source_id: str, ok: bool, *, rows: int | None = None, error: str | None = None,
                  meta: dict | None = None) -> dict:
    return {
        "source_id": source_id,
        "status": "CURRENT" if ok else "FAILED",
        "rows": rows,
        "error": error,
        "retrieved_at": (meta or {}).get("retrieved_at", utc_now()),
        "sha256": (meta or {}).get("sha256"),
        "source_url": (meta or {}).get("url"),
        "failure_is_zero": False,
    }


def public_table_candidates(source_id: str, url: str) -> tuple[pd.DataFrame, dict]:
    tables, meta = html_tables(url)
    records: list[pd.DataFrame] = []
    for table in tables:
        x = canonicalize_columns(table)
        if len(x) == 0:
            continue
        rcol = find_col(x.columns, ["rut", "r_u_t"])
        dvcol = find_col(x.columns, ["dv", "d_v", "digito_verificador"])
        ncol = find_col(x.columns, ["nombre_o_razon_social", "nombre_razon_social", "nombre", "organizacion"])
        if not rcol and not ncol:
            continue
        out = pd.DataFrame(index=x.index)
        out["source_id"] = source_id
        if rcol and dvcol:
            out["rut"] = [normalize_rut(r, d) for r, d in zip(x[rcol], x[dvcol])]
        elif rcol:
            out["rut"] = x[rcol].map(normalize_rut)
        else:
            out["rut"] = None
        out["entity_id"] = out["rut"].map(entity_id)
        out["legal_name"] = x[ncol].map(clean_text) if ncol else ""
        out["legal_name_norm"] = out["legal_name"].map(normalize_name)
        out["source_url"] = meta["url"]
        out["retrieved_at"] = meta["retrieved_at"]
        records.append(out)
    if not records:
        return pd.DataFrame(columns=["source_id", "rut", "entity_id", "legal_name", "legal_name_norm", "source_url", "retrieved_at"]), meta
    return pd.concat(records, ignore_index=True).drop_duplicates(), meta


def write_json(data: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
