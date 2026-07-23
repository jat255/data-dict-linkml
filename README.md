# LinkML → data-dict

This is an experiment find out whether a valid [`data-dict.yaml`](https://data-dict.tidyverse.org/)
file could be generated from a [LinkML](https://linkml.io/) schema, the same way
LinkML already emits JSON Schema, SHACL, Pydantic classes, etc. 
There's a small generator here, `gen_data_dict.py`, that
reads a LinkML schema and generates a valid `data-dict.yaml` schema. 
The output passes all three of data-dict's validators, and the same
dataset checks out under both toolchains.

## Files

| File | What it is |
|------|------------|
| `otters.linkml.yaml` | The source LinkML schema. Has inheritance, an enum, units, a foreign key, and a `tree_root` container. |
| `gen_data_dict.py` | The generator. LinkML schema in, `data-dict.yaml` out. Schema only, no data. |
| `data-dict.yaml` | Generated dictionary, portable (no data-location pointers). |
| `data-dict.test.yaml` | Same thing generated with `--emit-source`, which adds a `source:` pointer per table so the data checks can find the files. |
| `survey.data.yaml` | A data instance in LinkML's nested/object shape, for `linkml-validate`. |
| `otter.parquet`, `territory.parquet` | The same rows in columnar form, for data-dict. |
| `make_fixtures.py` | Rebuilds the Parquet files. |
| `loss.log` | What the generator dropped on the way over. |
| `site/` | Quarto site sources (`.qmd`, `_quarto.yml`). `quarto render site` builds `site/_site/`. |

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/). It runs the generator and the LinkML CLI
  tools (through `uvx`), so there's nothing to install by hand.
- The data-dict CLI. There's no release yet, so build it from source (see [data-dict's docs](https://data-dict.tidyverse.org/) for details):
  ```bash
  cargo install --git https://github.com/tidyverse/data-dict data-dict-cli
  ```
- [Quarto](https://quarto.org/), if you want to render the site.

## Running the validation

Everything runs from this directory:

```bash
cd /Users/josh/Documents/experiments/data-dict_linkml
```

### Part A: the LinkML side

Check the schema, then check a data instance against it.

```bash
# lint the schema
uvx --from linkml linkml-lint otters.linkml.yaml
#   ✓ No problems found

# validate the schema against the LinkML metamodel
uvx --from linkml linkml-validate otters.linkml.yaml
#   No issues found

# validate a data instance against the schema
uvx --from linkml linkml-validate -s otters.linkml.yaml -C Survey survey.data.yaml
#   No issues found
```

That last command checks the nested form in `survey.data.yaml`: enum values,
numeric bounds, and that each otter's `territory: <n>` points at a real Territory.

### Part B: generate the dictionary

```bash
# portable version, for validate-spec
uv run gen_data_dict.py otters.linkml.yaml > data-dict.yaml

# same thing plus source: pointers, for the data-level checks
uv run gen_data_dict.py otters.linkml.yaml --emit-source > data-dict.test.yaml
```

`--emit-source` writes `source: {parquet: <table>.parquet}` into each table.
That's a file path, not data, so the generator still reads nothing. The
generator prints what it couldn't carry across to stderr. For this schema that's
two lines (a dropped URI namespace, and the flattened `NamedThing` parent) and
zero unfilled columns.

### Part C: the data-dict side

```bash
data-dict validate-spec data-dict.yaml        # structure
data-dict validate-meta data-dict.test.yaml   # column names and types vs Parquet
data-dict validate-data data-dict.test.yaml   # values vs constraints
```

All three come back `ok`. If you want to see a validator actually reject
something, bump a `weight` past 45 in `make_fixtures.py`, rerun it, and run
`validate-data` again.

## What I learned

The thing I got wrong at first was assuming data-dict needed real data to fill
in `examples` and value ranges. It doesn't. Those are ordinary LinkML slot
annotations (`examples:`, `minimum_value`, `maximum_value`), they're just easy to
leave off. Once the schema carries them, the whole dictionary comes out valid
with no data anywhere in sight.

What genuinely doesn't survive the trip is anything semantic. data-dict has no
place for URIs or ontology mappings, so those get dropped. It's also flat, so a
LinkML abstract parent like `NamedThing` disappears and its fields get copied
down into each table that inherited them. Neither is really a LinkML problem;
data-dict just chose to stay lighter than that.

The interesting part is the foreign key. LinkML says "this slot's value is a
Territory" (a reference to another object). data-dict says "this is an FK column,
and here's the join." The generator translates between the two. That split is
also why there are two copies of the data: `survey.data.yaml` is the nested shape
LinkML validates, and the Parquet files are the flat shape data-dict validates.
Same rows either way.

The [Findings](site/findings.qmd) page has the longer version.

## Docs site

```bash
quarto render site      # builds site/_site/
quarto preview site     # local preview with live reload
```
