#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "polars",
# ]
# ///
"""Create Parquet fixtures matching the otters data-dict tables.

Columns and types are chosen to line up with the generated data-dict.yaml so we
can exercise validate-meta (names/types) and validate-data (values/constraints).
"""
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent

# Territory: area(float), protected(bool), id(int pk), name(string)
territory = pl.DataFrame(
    {
        "area": [12.5, 3.2, 40.0],
        "protected": [True, False, True],
        "id": [1, 2, 3],
        "name": ["Riverbend", "Kelp Cove", "Amazon Reach"],
    }
)
territory.write_parquet(HERE / "territory.parquet")

# Otter: species(enum->str), weight(float), age_years(int),
#        territory(fk int), id(int pk), name(string)
otter = pl.DataFrame(
    {
        "species": ["lutra_lutra", "enhydra_lutris", "pteronura_brasiliensis"],
        "weight": [8.1, 30.0, 26.0],
        "age_years": [3, 7, 5],
        "territory": [1, 2, 3],
        "id": [101, 102, 103],
        "name": ["Ollie", "Sandy", "Rio"],
    }
)
otter.write_parquet(HERE / "otter.parquet")

print("wrote territory.parquet and otter.parquet")
print("\n-- territory schema --")
print(territory.schema)
print("\n-- otter schema --")
print(otter.schema)
