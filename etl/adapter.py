"""Adapter layer — one shared, declarative mapping engine.

A single ``adapt`` function turns any bronze source into the canonical column
shape by reading its YAML mapping spec. The *spec* is what varies per source;
the code does not. Adding a 4th POS source therefore requires only a new YAML
file under ``etl/mappings/`` and an entry in ``config.POS_SOURCES`` — zero
changes here. That is the deliberate extensibility point.

Supported transform types (keyed by canonical column in the spec):
  - ``datetime``                  parse with a strptime ``format`` (bad -> NaT)
  - ``currency_string_to_float``  ``strip`` a symbol, parse float (bad -> NaN)
  - ``cents_to_float``            integer minor units / 100
  - ``cad_to_usd``                multiply by the POC FX constant
  - ``unit_price_from_line_total``derive from native ``line_total``/``quantity``
                                  columns, optionally converting ``currency``
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from etl import config

logger = config.get_logger("etl.adapter")


@lru_cache(maxsize=None)
def load_mapping(source: str) -> dict:
    """Load and cache the YAML mapping spec for a source."""
    path: Path = config.MAPPINGS_DIR / f"{source}.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)
    if spec.get("source") != source:
        raise ValueError(f"Mapping spec {path} declares source={spec.get('source')!r}, expected {source!r}")
    return spec


def _apply_transform(canon: pd.DataFrame, bronze: pd.DataFrame, field: str, spec: dict) -> None:
    ttype = spec["type"]
    if ttype == "datetime":
        canon[field] = pd.to_datetime(canon[field], format=spec.get("format"), errors="coerce")
    elif ttype == "currency_string_to_float":
        strip = spec.get("strip", "")
        s = canon[field].astype("string")
        if strip:
            s = s.str.replace(strip, "", regex=False)
        s = s.str.replace(",", "", regex=False).str.strip()
        canon[field] = pd.to_numeric(s, errors="coerce")
    elif ttype == "cents_to_float":
        canon[field] = pd.to_numeric(canon[field], errors="coerce") / 100.0
    elif ttype == "cad_to_usd":
        canon[field] = pd.to_numeric(canon[field], errors="coerce") * config.CAD_USD_RATE
    elif ttype == "unit_price_from_line_total":
        line_total = pd.to_numeric(bronze[spec["line_total"]], errors="coerce")
        qty = pd.to_numeric(bronze[spec["quantity"]], errors="coerce")
        if spec.get("currency") == "CAD":
            line_total = line_total * config.CAD_USD_RATE
        canon[field] = (line_total / qty.replace(0, pd.NA)).round(2)
    else:
        raise ValueError(f"Unknown transform type: {ttype!r} for field {field!r}")


def adapt(bronze_df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Map a bronze source frame to the canonical Silver column shape."""
    spec = load_mapping(source)
    field_map: dict[str, str] = spec.get("field_map", {})
    transforms: dict[str, dict] = spec.get("transforms", {})

    canon = pd.DataFrame(index=bronze_df.index)
    # 1. Rename native -> canonical for every directly-mapped field.
    for native_col, canonical_col in field_map.items():
        canon[canonical_col] = bronze_df[native_col]

    # 2. Apply transforms (derived fields like unit_price are created here).
    for field, tspec in transforms.items():
        _apply_transform(canon, bronze_df, field, tspec)

    # 3. Coerce canonical types; coalesce optional discount to 0.
    canon["source_txn_id"] = canon["source_txn_id"].astype("string")
    canon["sku"] = canon["sku"].astype("string")
    canon["store_id"] = canon["store_id"].astype("string")
    canon["quantity"] = pd.to_numeric(canon.get("quantity"), errors="coerce")
    canon["unit_price"] = pd.to_numeric(canon.get("unit_price"), errors="coerce")
    canon["discount_amount"] = pd.to_numeric(canon.get("discount_amount"), errors="coerce").fillna(0.0)

    # 4. Carry forward the bronze audit columns the Silver layer needs.
    canon["source_system"] = bronze_df["source_system"].values
    canon["batch_id"] = bronze_df["batch_id"].values
    canon["ingested_at"] = bronze_df["ingested_at"].values

    return canon[config.CANONICAL_COLUMNS]
