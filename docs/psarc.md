# PSARC container

[Documentation index](./README.md) | [Known unknowns](./known-unknowns.md)

## Supported profile

The importer reads the PSARC containing the supported retail
`data_ps3.psarc`. The following values are **enforced**:

- magic: ASCII `PSAR`;
- version: `0x00010003`;
- compression tag: ASCII `zlib`;
- TOC entry size: 30 bytes;
- at least one entry;
- the first entry is a UTF-8 path manifest.

The reader is extraction-only. It does not create or modify PSARC archives.

## Header

All fields are big-endian.

| Offset | Size | Field | Importer behavior |
|---:|---:|---|---|
| `0x00` | 4 | Magic | Must be `PSAR` |
| `0x04` | 4 | Version | Must be `0x00010003` |
| `0x08` | 4 | Compression | Must be `zlib` |
| `0x0C` | 4 | TOC length | Absolute end of the TOC/block-length table |
| `0x10` | 4 | TOC entry size | Must be 30 |
| `0x14` | 4 | Entry count | Includes the manifest entry |
| `0x18` | 4 | Logical block size | Must be nonzero |
| `0x1C` | 4 | Flags | Preserved but not interpreted |

The fixed TOC records start at `0x20`. The TOC length must be at least
`0x20 + entry_count * 30` and no larger than the archive.

## TOC entry

Each 30-byte entry has this layout:

| Relative offset | Size | Field |
|---:|---:|---|
| `0x00` | 16 | Path digest |
| `0x10` | 4 | Index of the first block-length record |
| `0x14` | 5 | Uncompressed entry size (`u40`) |
| `0x19` | 5 | Absolute archive data offset (`u40`) |

The first record describes the manifest. Its digest is not used for path
lookup. Every later record is paired with one manifest line.

The importer identifies physically shared records by the tuple
`(data_offset, uncompressed_size, first_block_index)`. Multiple manifest paths
with that tuple are retained as aliases. Treating this tuple as physical
identity is an **implementation interpretation**, not a decoded on-disk alias
flag.

## Block-length table

The block-length table follows the fixed TOC records and ends at
`toc_length`. One length uses:

```text
max(1, ceil(log2(block_size) / 8)) bytes
```

Each value is big-endian. For an entry:

```text
block_count = ceil(uncompressed_size / block_size)
```

Extraction starts at the entry's `first_block_index` and `data_offset`.
For each logical block:

1. The expected decoded length is the smaller of `block_size` and the
   entry bytes remaining.
2. A stored block-length value of zero means one uncompressed block occupying
   `block_size` bytes.
3. A nonzero stored length equal to the expected decoded length is also treated
   as uncompressed.
4. Any other nonzero length is read and passed to zlib.
5. The decoded output must exactly match the expected decoded length.

The zero-length rule means a final uncompressed partial block is expected to
have its actual size in the table, not zero. A same-size zlib stream would be
treated as raw data; whether such a block can occur in other PSARC writers is
unknown.

## Manifest and path lookup

Entry zero is extracted before other paths are known. The importer enforces a
64 MiB maximum manifest size, decodes it as UTF-8, and splits it into lines.
The number of lines must be exactly `entry_count - 1`.

For each logical path, the expected digest is:

```text
MD5(UTF8(path.upper()))
```

The 16-byte result must equal the corresponding TOC digest. Lookup is
case-insensitive through Python `casefold()`, while the original spelling is
preserved for diagnostics.

The entity scan considers only paths ending in `.core`, case-insensitively,
and deduplicates physical aliases before parsing. Level discovery additionally
uses the path patterns documented in
[Levels and entities](./levels-entities.md).

## Bounds and safety limits

| Limit | Value |
|---|---:|
| Maximum TOC entry count | 10,000,000 |
| Maximum manifest size | 64 MiB |
| TOC entry size | Exactly 30 bytes |

Every entry start, block-index range, stored block, and decoded block length is
bounds-checked. An entry may be read fully or only through a requested prefix;
prefix reads still decode whole PSARC blocks until enough output is available.

## Unknowns and unsupported variants

- The `flags` field is retained but its bit meanings are unknown.
- Only the literal `zlib` compression profile is accepted. Other PSARC
  compression tags, encryption schemes, or version values have not been
  characterized here.
- Manifest encoding is assumed to be UTF-8, and the path hash uses Unicode
  uppercasing before UTF-8 encoding because that matches the supported
  archive. Behavior for non-ASCII paths has not been independently validated.
- The reader does not infer paths from hashes when the manifest is absent or
  damaged.
- The physical-alias tuple is sufficient for the supported archive, but no
  separate alias metadata has been identified.
- Archive alignment and the semantic meaning of version/flag bits beyond the
  enforced profile remain undocumented.
