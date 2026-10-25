# Killzone 2 file-format notes

These notes describe the binary formats that the importer currently accepts.
They are intended for technical artists, preservation researchers, and analysts
who need to inspect assets or compare another tool with this implementation.

> [!IMPORTANT]
> This is an implementation-oriented, reverse-engineered description, not an
> official Guerrilla Games or Sony specification. It is intentionally limited
> to the US retail `BCUS98116`, app/disc version `01.01`,
> `RTTIBin<1.58>` profile. A field name describes how this importer uses a
> value; it does not prove the original engine name or purpose.

No game data is included in this repository or in these notes.

## What the importer accepts

| Form | How it is supplied | Current contract |
|---|---|---|
| `data_ps3.psarc` | Selected in the KZ2 Tools settings, or supplied as the source archive for an extracted CORE | PSARC `0x00010003`, `zlib`, 30-byte TOC entries, UTF-8 manifest |
| Extracted `.core` | **File > Import > Killzone 2 CORE (.core)** | Big-endian `RTTIBin<1.58>` object graph with endian marker `1` |
| CORE entries inside the PSARC | Selected through the level/entity catalog or loaded as external dependencies | Same CORE profile as an extracted file |

The following are **embedded subformats**, not standalone formats exposed by
the Blender file picker:

- `ES00` skeleton data inside a `Skeleton` CORE object;
- `EA02` animation data inside a `SkeletonAnimationResource`;
- RSX texture descriptors and image payloads inside a `Texture` object;
- `SHO\x08` compiled shader objects inside a `ShaderResource`;
- big-endian Lua 5.1 chunks inside zero-sized `GameScript` direct objects.

The JSON entity catalog and SQLite resource index created by a scan are
implementation caches. They are not source-asset formats and should not be
treated as archival representations.

## Confidence labels

The notes use these labels to keep structural facts separate from semantic
interpretation:

- **Enforced**: the current parser checks this value, size, relationship, or
  signature and rejects a mismatch.
- **Retail-validated**: the behavior has also been exercised against the
  supported retail archive or a documented Blender integration sample.
- **Interpretation**: the bytes are framed consistently, but the field name or
  runtime meaning is inferred from behavior, neighboring types, shader traces,
  or output.
- **Unknown/raw**: the parser preserves or skips the bytes without assigning
  semantics.

An enforced rule is the importer's compatibility contract, not proof that all
valid engine files must obey it. Other regions, patches, development builds,
or later engine revisions may use valid layouts that this importer rejects.

## Format references

- [PSARC container](./psarc.md): header, TOC, manifest, block decoding, and
  alias handling.
- [RTTIBin CORE](./core.md): object graph, compact integers, string banks,
  references, payload framing, and registered object types.
- [Geometry, textures, and materials](./geometry-materials.md): vertex
  declarations, mesh wrappers, skinning, RSX texture payloads, render effects,
  and preview-material boundaries.
- [Levels and entities](./levels-entities.md): level manifests, zero-sized
  direct serializers, placement records, entity/model links, and
  destructibility.
- [Skeletons and animation](./animation.md): expanded skeletons, `ES00`,
  animation wrappers, `EA02`, channel packing, and sampling limitations.
- [Known unknowns and hypotheses](./known-unknowns.md): consolidated research
  gaps, heuristics, and unsupported variants.

## General binary conventions

- Multi-byte scalar fields are big-endian unless a format page explicitly
  says otherwise. Notable exceptions are the little-endian words inside
  S3TC/DXT texture blocks.
- CORE-relative layouts are described from the beginning of the relevant
  object payload, after any variable-length resource prefix.
- A `CoreReference` can be null, point to an object in the same CORE, or carry
  an external opaque identifier. External references need the matching source
  PSARC to resolve.
- Parsers are bounded and fail closed on unsupported signatures, revisions,
  direct serializers, and animation flags rather than fabricating data.

## Useful analysis entry points

The pure-Python readers do not require Blender:

```python
from io_scene_killzone2.kz2_assets import CoreDecoder, CoreFile, Psarc

archive = Psarc.open(r"E:\path\to\data_ps3.psarc")
entry = archive.get("LocalCachePS3/lumps/example.core")
core = CoreFile.from_bytes(archive.read_entry(entry), source=entry.path)
decoder = CoreDecoder(core)
```

To inventory the supported retail archive without writing extracted assets:

```powershell
python .\tools\audit_retail_archive.py `
  "E:\path\to\data_ps3.psarc" `
  --output .\kz2-audit.json
```

The audit report includes paths, type counts, payload sizes, reference
statistics, animation signatures, and bounded failure diagnostics, but no
serialized asset payloads.
