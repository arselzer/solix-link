# Android type-3 archive callback

## Scope and result

This continues [container preparation](android-loader-container-record.md)
for the exact retained APK, SHA-256
`27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110`.
The static proof resolves the previously unknown `context+48 → +20` callback
to **`54e6c`**, an archive entry reader. It does not recover the runtime key,
plaintext SDK DEX or charging-pause encoder. No guest, VM, JNI, Android,
initializer, transform or native callback executes.

The recovered original carrier is SHA-256
`b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57`;
the earlier initialized image is
`14c73475f02e708a07d982b65d5fcea65792c8d477ba293ca95187f461e6e06f`.
Original ELF relocations establish pointers without loading code. The tool
hash-checks both snapshots and the packed library before creating output.

## Context interface and archive extraction boundary

Initializer **`54954..54998`** loads the global context through alias `f8ab0 →
fd220` and stores table **`fe768`** at context offset `48`. Eight original
`R_AARCH64_RELATIVE` entries identify that table's functions. Its slot `20`
points to **`54e6c..54f44`**, matching the indirect call at **`b2ed8`**.
This is conditional static provenance: the initializer's runtime invocation
and the selected asset filename remain unproved.

The callback's actual instructions prepare these operations:

| Address | Operation |
| --- | --- |
| `54e9c` | Lock a mutex; original PLT relocation names `pthread_mutex_lock` |
| `54ea8 → 827a4 → 82b50` | Open the supplied buffer and length as an archive |
| `54ebc → 82840` | Find the supplied entry name by length and `memcmp` |
| `54ec8 → 82918` | Obtain the entry's uncompressed size |
| `54ee8` | Allocate output; original PLT relocation names `malloc` |
| `54f00 → 82970` | Copy/decompress the entry; zero return becomes success |

The earlier caller supplies the fixed name **`classes.dex`**. Parser `82b50`
scans backward for ZIP end-of-central-directory bytes **`50 4b 05 06`**.
Entry extraction `82970` branches on compression method: stored method 0
uses `memcpy`; method 8 prepares a raw-deflate stream with window bits `-15`
and reaches the inflate-like helpers `8e0e4`, `8e3e4` and `8ffd8`.
Unsupported-method/error paths are also present. No archive or decompressor
is invoked by this proof, and no plaintext output is asserted.

The retained **`ijiami.dat` has type 3**, as established in the preceding
batch. The independent type-2 inflate route does not establish a decoder for
that asset. The useful boundary is now: selected type-3 payload → unresolved
keyed preparation → this archive callback → candidate `classes.dex` buffer.

## Runtime-key writer investigation

A bounded scan of **2,040 original FDE functions / 169,679 decoded instructions**
finds 32 immediate `+160` memory candidates, including pair stores at `+158`.
It explicitly excludes literal SP-relative operands, but register aliases of
stack buffers remain candidates. Two apparent pointer writes, `6e9b4` and
`6e9cc`, address a **different symbol table at `11a0f8`**, not the runtime
context. Their function constructs that table base before resolving symbols.
They cannot establish the transform key's provenance.

This scan is not exhaustive alias analysis: indirect addresses, bulk copies,
protector-VM stores and code outside supported FDE spans remain open. It
does not prove the key field is unwritten. The actual writer/value of
`context+160` and the selected asset filename are still precise prerequisites.
The already identified selector-2 wrapper at `43308`, its descriptor at
`10c430 + 2*8`, and native/VM context construction are further static leads.
No keys, seeds, raw records, vendor code or SDK action bytes are published.

The subsequent [selector-2 proof](android-selector-two-record.md) covers all
410 selected record bytes and identifies its initial context `+128` access
and three unexecuted allocator calls. It still does not resolve the key writer.

## Reproduction

```sh
python3 tools/firmware_analysis/inspect_android_archive_callback.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /private/output/archive-callback
```

Compare result and manifest with `expected_results/android-archive-callback-*.json`.
The new proof performs **44 exact instruction checks**, eight interface
relocation checks, six PLT-symbol resolutions and eight FDE span hashes.
Verified here with Python **3.14.4**, Capstone **5.0.7**; guest instructions: **0**.
The initialized image is only hash-checked, not rerun or used to export strings.
