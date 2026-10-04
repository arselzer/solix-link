# Protected container preparation: complete static record

This extends the [ClassLoader boundary](android-loader-classloader-boundary.md)
for the same retained Android APK. It follows the ClassLoader preparation
record into a native container reader. It does not recover a charging-pause
packet or establish device/model support.

The later [archive-callback proof](android-archive-callback.md) resolves the
type-3 interface callback to a ZIP entry reader. The runtime key and selected
asset filename remain unresolved; earlier batch limits below are preserved.

The subsequent [selector-2 analysis](android-selector-two-record.md) completes
the next 410-byte record's static coverage; its context/key alias remains open.

## Pinned scope and reproduction

The APK SHA-256 is
`27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110`.
The selected library is `assets/ijm_lib/arm64-v8a/libexec.so`; its packed and
recovered image hashes are recorded in the manifest. The prior initialized
image is also hash pinned and read only. This tool does not replay its
initializers.

Run from the repository root with the retained inputs and analysis dependencies:

```bash
PYTHONPATH=/tmp/solix-analysis-tools python3 \
  tools/firmware_analysis/inspect_android_loader_container_record.py \
  --base-apk .solix-private/app-binary/base.apk \
  --images-dir .solix-private/sdk-loader-investigation-20261002/public-reproduction-images \
  --initialized-image .solix-private/sdk-loader-investigation-20261002/libexec.so.decoded-strings-memory.bin \
  --output-dir /tmp/android-loader-container-record-review
```

Compare both JSON files with
[`android-loader-container-record-results.json`](../tools/firmware_analysis/expected_results/android-loader-container-record-results.json)
and its
[manifest](../tools/firmware_analysis/expected_results/android-loader-container-record-manifest.json).
The verified environment was Python 3.12.3 and Capstone 5.0.7. The tool imports
frozen helper definitions, applies original ELF relative relocations as byte
operations, and disassembles selected AArch64 spans. It executes zero guest,
VM, JNI, Android or native initializer instructions. It performs no asset
transform, host callback, network operation or station command.

## Full selector-zero coverage

The wrapper at `0xb20c4..0xb20fc` supplies:

| Field | Offset/value |
| --- | --- |
| Native call table | `0x118a40` |
| Native thunk table | `0x118ac0` |
| Record base | `0x118b30` |
| Descriptor table | `0x11919c` |
| Selector | `0` |
| Selected descriptor | offset `0`, length `1,644` |

The record SHA-256 is
`11889935baf88763cb254647ed77bd71f343b12714e578d6d007982b8c6dae1e`.
Its four-byte header encodes zero initial frame words; the first token then
reserves 334 signed-count words. Parsing covers **all 1,640 body bytes**:
530 rolling-key states, 525 distinct token offsets, 14 native call tokens,
and one void-return terminal at offset `1,643`. Five token offsets have
multiple incoming rolling-key states; their token identities agree.

Both edges of conditional branches are retained. Signed 24-bit backward
displacements are handled. Native results, the VM stack and runtime memory
remain symbolic. Complete byte coverage establishes the token/control-flow
description; it does not establish which branches the running app takes or
whether native callbacks succeed.

The 15 newly verified handlers have zero operand bytes: void return, byte/word
indirect loads/stores, pointer comparisons, signed word comparison, word XOR/OR,
pointer AND/multiply/arithmetic shift, and word-to-pointer extensions. Each is
checked against its dispatch pointer, complete span hash, selected semantic
instructions and exact common-advance branch. Both extension handlers use
`0xc45f0..0xc4600`, which advances the token cursor by one byte. These are
protector handler indices, separate from station protocol opcodes.

## Container-reader call and asset-type correction

The record loads native table slot `4` at offset `249`, pushes locals
`23`, `4`, `38`, `6`, and calls thunk `3` at offset `264`.
The table's original ELF relative relocation resolves slot `4` to `0xb2dbc`.
The full thunk `0xee304..0xee3bc` reconstructs four 64-bit pointer arguments,
calls the native target, and puts `w0 & 1` back on the VM stack.

The arguments are a prepared filename buffer and out-buffer, out-length and
out-type cells. The result becomes a symbolic conditional at offset `272`:
nonzero goes to `784`, zero to `276`. The actual selected filename and the
runtime asset-reader interfaces remain unresolved.

The native reader `0xb2dbc..0xb3278` first reads a 40-byte header. Its routes
depend on the first 32-bit word:

| Header type | Exact native route | Retained selected asset evidence |
| --- | --- | --- |
| `2` | `0xb6b34`; its separate transform skips 44 bytes and reaches an inflate-like helper | None of the three selected asset headers is type 2 |
| `3` | Read full asset at `0xb2e78`; skip 40 bytes; call `0xb6d44` at `0xb2ea8`; call runtime interface `context+0x48 → +0x20` at `0xb2ed8` | `assets/ijiami.dat`, 8,847,278 bytes, has type 3 |

`IJMDal.Data` and `ijiami.ajm` have other header words. All three selected
assets lack a leading DEX magic signature. Their lengths/hashes and coarse
classification are in the public metadata; raw headers/assets are private.
The type 3 header matches a candidate route if `ijiami.dat` is selected;
actual runtime filename selection has not been proved.

At `0xb2ed8`, the callback receives the transformed payload, signed payload
length, fixed name `classes.dex`, and out-pointer/out-length cells. A successful
result writes the outputs plus `context+0x2b8` and zero at `+0x2c0`. This is a
concrete extraction boundary. The callback's implementation and plaintext
output are still unknown.

The earlier type 2 helper remains documented as a candidate in the ClassLoader
batch. It does not describe the header of the retained `ijiami.dat` file.

## Runtime key and remaining native wrapper

`0xb6d44` copies a 30-byte static seed from `0x10a0d0` and derives 26 bytes:

```text
key[i] = static_seed[i + 1] XOR runtime_context_pointer160[i + 3]
```

The runtime pointer is loaded from `context+0x160`; the context alias is
statically tied to global pointer cell `0xfd220` through `0xf8ab0`. The writer
and actual value of `+0x160` have not been recovered. The protected record also
references a separate static seed at `0x10a140`; these two addresses are kept
distinct. Public metadata contains lengths and hashes, with no seed/key bytes.
The native transform includes selective payload ranges and remainder handling;
no whole-file periodic XOR or working decoder is asserted or attempted.

Another reachable call, token `1,539` / table slot `5`, resolves to
`0x43308..0x43374`. It is another protected wrapper: it forwards the original
pointer and low-byte argument, then enters selector `2` using call table
`0x10b3e0`, thunks `0x10b500`, record base `0x10b660`, and descriptors
`0x10c430`. That next record is unparsed in this batch. Native record-list
construction/commit and the successful ClassLoader buffer producer remain
unresolved. `DETool.dowork` data protection is a separate investigation.

## Verification and next bounded lead

The tool performs 1,063 exact static instruction checks, validates 15 original
call-table relocations and 14 complete FDE-bounded thunks, and includes 19
negative record/relocation tests. Altered APK, recovered image and initialized
image copies were independently rejected before output creation. Token
ownership prevents branches into an existing operand; state growth is capped
at 768. All raw evidence stays in the ignored owner-only private directory.

The next useful static leads are the runtime `+0x160` writer, the type 3
interface `+0x20` implementation, and the selector-2 wrapper's record. Current
evidence supplies no plaintext SDK action encoder, MQTT/BLE frame mapping,
account-free setup claim, or equivalence across Anker models/firmware.
