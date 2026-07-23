#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "linkml-runtime",
#     "pyyaml",
# ]
# ///
"""gen-data-dict: a proof-of-concept LinkML -> data-dict.yaml generator.

Emits the *relational projection* of a LinkML schema as a data-dict.yaml
($version 0.1.0).

Built against the data-dict CLI 0.0.1, tidyverse/data-dict commit
c146baec42fceb360252d7670663bddb1f6dcfc7 (installed from main; no tagged
releases yet). data-dict is early and still changing, so the S07 type rules
below and this output may not hold on newer commits.

Each data-dict column type wants a specific representation (per data-dict's
validate_spec.rs, rule S07), and LinkML supplies each one:

    enum         -> `values`                 from `permissible_values`
    boolean      -> none of the three        trivially
    range types  -> `range: [min, max]`      from `minimum_value`/`maximum_value`
      (number(ordinal|quantity), date, datetime)
    example types-> NON-EMPTY `examples`      from slot `examples:` (use
      (string, number, number(id))            `slot_usage` for per-table values)

Where the schema omits an `examples:` list or a bound, the generator emits the
column as a flagged SCAFFOLD hole; annotate the LinkML to close it.

Losses data-dict has no concept for:
  * URIs / prefixes / ontology mappings -> dropped.
  * Inheritance: abstract/mixin classes are NOT tables; their slots are
    FLATTENED into each concrete descendant via class_induced_slots().
Both are reported to stderr as a "lossiness log".

Also: a class-valued slot becomes a foreign_key column + a relationships[] entry,
and identifier columns are hoisted to the front of each table.

Usage:
    python gen_data_dict.py SCHEMA.yaml > data-dict.yaml
    python gen_data_dict.py SCHEMA.yaml --emit-source > data-dict.test.yaml

--emit-source adds a `source: {parquet: <table>.parquet}` pointer to each table
(convention: lowercased class name), so `validate-meta`/`validate-data` can find
the files. The default (no flag) stays portable and source-free.
"""
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict

import yaml
from linkml_runtime import SchemaView


# ---------------------------------------------------------------------------
# YAML emission: keep insertion order, block style, no aliases.
# ---------------------------------------------------------------------------
class _OrderedDumper(yaml.SafeDumper):
    pass


def _dict_representer(dumper, data):
    return dumper.represent_mapping(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, data.items())


_OrderedDumper.add_representer(OrderedDict, _dict_representer)
_OrderedDumper.ignore_aliases = lambda *_a: True  # type: ignore[assignment]


LOSS: list[str] = []


def note_loss(msg: str) -> None:
    LOSS.append(msg)


# ---------------------------------------------------------------------------
# data-dict type system (from validate_spec.rs).
# ---------------------------------------------------------------------------
RANGE_TYPES = {"number(ordinal)", "number(quantity)", "date", "datetime"}
EXAMPLE_TYPES = {"string", "number", "number(id)"}  # boolean/enum handled apart

_NUMERIC = {"integer", "float", "double", "decimal"}


def map_type(sv: SchemaView, slot, is_enum: bool, is_fk: bool) -> str:
    if is_enum:
        return "enum"
    if is_fk:
        return "number(id)"  # FK carries the referenced table's identifier
    rng = slot.range or sv.schema.default_range or "string"
    if slot.identifier and rng in _NUMERIC:
        return "number(id)"
    if rng in _NUMERIC:
        return "number(quantity)" if slot.unit else "number"
    if rng == "boolean":
        return "boolean"
    if rng == "date":
        return "date"
    if rng == "datetime":
        return "datetime"
    if rng == "string":
        return "string"
    note_loss(f"slot '{slot.name}': range '{rng}' has no data-dict type; mapped to string")
    return "string"


def _coerce_example(dd_type, val):
    """LinkML Example.value is always a string; give numeric columns real numbers."""
    if dd_type in ("number", "number(id)", "number(ordinal)", "number(quantity)"):
        try:
            return int(val) if "." not in str(val) else float(val)
        except (TypeError, ValueError):
            return val
    return val


def build_column(sv, cls_name, slot, enums, classes, relationships):
    col = OrderedDict()
    col["name"] = slot.name
    if slot.title:
        col["label"] = slot.title
    if slot.description:
        col["description"] = slot.description

    rng = slot.range
    is_enum = rng in enums
    is_fk = rng in classes
    dd_type = map_type(sv, slot, is_enum, is_fk)
    col["type"] = dd_type

    # Constraints ----------------------------------------------------------
    constraints = []
    if slot.identifier:
        constraints.append("primary_key")
    if is_fk:
        constraints.append("foreign_key")
    if slot.required:
        constraints.append("required")
    if constraints:
        col["constraints"] = constraints

    # Units (quantity only) ------------------------------------------------
    if slot.unit and getattr(slot.unit, "ucum_code", None):
        col["units"] = slot.unit.ucum_code

    # Report facets with no home ------------------------------------------
    if slot.pattern:
        note_loss(f"slot '{slot.name}': regex pattern dropped (no data-dict equivalent)")
    if getattr(slot, "any_of", None) or getattr(slot, "all_of", None):
        note_loss(f"slot '{slot.name}': logical constraint dropped")

    # Representation: EXACTLY the one data-dict requires for this type ------
    # LinkML stores Example.value as a string; coerce to the column's type so
    # data-dict sees numbers as numbers (matches the clean-two-tables fixture).
    examples = [
        _coerce_example(dd_type, e.value)
        for e in (slot.examples or [])
        if e.value is not None
    ]

    if dd_type == "enum":
        pvs = sv.get_enum(rng).permissible_values
        if any(pv.description or pv.title for pv in pvs.values()):
            col["values"] = OrderedDict(
                (k, pv.title or pv.description or k) for k, pv in pvs.items()
            )
        else:
            col["values"] = list(pvs.keys())

    elif dd_type == "boolean":
        pass  # must carry none of values/range/examples

    elif dd_type in RANGE_TYPES:
        lo = slot.minimum_value
        hi = slot.maximum_value
        if lo is not None and hi is not None:
            col["range"] = [lo, hi]
        else:
            note_loss(
                f"INCOMPLETE SCHEMA: column '{cls_name}.{slot.name}' ({dd_type}) needs "
                f"range [min,max]; LinkML declares "
                f"{'no' if lo is None and hi is None else 'only one'} bound. Add "
                f"minimum_value/maximum_value to the slot."
            )
            col["range"] = [lo, hi]  # partial -> fails validation, surfaces the gap

    else:  # EXAMPLE_TYPES: string, number, number(id)
        if not examples:
            note_loss(
                f"INCOMPLETE SCHEMA: column '{cls_name}.{slot.name}' ({dd_type}) needs "
                f"non-empty examples; none declared. Add `examples:` to the LinkML slot "
                f"(use slot_usage for per-table values)."
            )
        col["examples"] = examples

    # Foreign key -> relationships[] ---------------------------------------
    if is_fk:
        target = sv.get_class(rng)
        tid = sv.get_identifier_slot(target.name)
        tid_name = tid.name if tid else "id"
        relationships.append(
            OrderedDict(
                join=f"{cls_name}.{slot.name} = {rng}.{tid_name}",
                cardinality="one-to-many" if slot.multivalued else "many-to-one",
                description=f"Each {cls_name} references a {rng}.",
            )
        )

    return col


def _ordered_slots(sv, cls_name):
    """Induced slots (inherited flattened in), identifier hoisted to front."""
    slots = list(sv.class_induced_slots(cls_name))
    slots.sort(key=lambda s: (not bool(s.identifier),))  # identifier first, stable
    return slots


def generate(schema_path, emit_source=False):
    sv = SchemaView(schema_path)
    schema = sv.schema

    enums = set(sv.all_enums().keys())
    all_classes = sv.all_classes()
    class_names = set(all_classes.keys())
    # Tables = concrete classes. Abstract/mixin are flattened into descendants;
    # a tree_root container is a document wrapper, not a table.
    concrete = [
        n for n, c in all_classes.items()
        if not c.abstract and not c.mixin and not c.tree_root
    ]

    doc = OrderedDict()
    doc["$version"] = "0.1.0"
    # $learn_more points at the data-dict spec (a constant), so always emit it;
    # a schema `see_also` may override the default. Clears warning S09.
    doc["$learn_more"] = schema.see_also[0] if schema.see_also else "http://data-dict.tidyverse.org/"
    doc["name"] = schema.name
    if schema.title:
        doc["label"] = schema.title
    if schema.description:
        doc["description"] = schema.description

    relationships = []
    tables = []
    for cls_name in concrete:
        cls = all_classes[cls_name]
        table = OrderedDict()
        table["name"] = cls_name
        if cls.title:
            table["label"] = cls.title
        if cls.description:
            table["description"] = cls.description
        if emit_source:
            # A location pointer, not data. Convention: lowercased class name.
            table["source"] = OrderedDict(parquet=f"{cls_name.lower()}.parquet")
        table["columns"] = [
            build_column(sv, cls_name, s, enums, class_names, relationships)
            for s in _ordered_slots(sv, cls_name)
        ]
        tables.append(table)

    doc["tables"] = tables
    if relationships:
        doc["relationships"] = relationships

    subsets = sv.all_subsets()
    if subsets:
        doc["glossary"] = OrderedDict((n, s.description or "") for n, s in subsets.items())

    if schema.version:
        doc["version"] = OrderedDict(number=schema.version)

    if schema.prefixes:
        note_loss("schema prefixes/URIs dropped (data-dict has no URI concept)")
    for n, c in all_classes.items():
        if c.abstract or c.mixin:
            note_loss(f"class '{n}': abstract/mixin flattened away (inheritance lost)")

    return yaml.dump(doc, Dumper=_OrderedDumper, sort_keys=False, default_flow_style=False)


def main():
    ap = argparse.ArgumentParser(description="Generate data-dict.yaml from a LinkML schema.")
    ap.add_argument("schema")
    ap.add_argument(
        "--emit-source",
        action="store_true",
        help="Emit a `source: {parquet: <table>.parquet}` pointer per table.",
    )
    args = ap.parse_args()

    sys.stdout.write(generate(args.schema, emit_source=args.emit_source))
    if LOSS:
        print("\n# ---- lossiness log (stderr) ----", file=sys.stderr)
        for msg in LOSS:
            print(f"# LOSS: {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
