# SDK bulk-copy candidate and runtime-context provenance

## New static boundary

The earlier immediate-store inventory found `stp` at **`dd1ac`**, whose
destination offset is **160 hexadecimal**. A bounded static follow-up now
pins its full function and direct callers. This follows the
[selector-2 dataflow investigation](android-selector-two-dataflow.md).

At **`dd14c..dd1f4`**, the original argument 0 becomes the destination base
`x20`, and argument 1 becomes the source base `x19`. The candidate operation
copies **32 bytes from `argument1+150` to `argument0+160`**. It does not derive
a key or establish either argument's alias to the runtime context cell
`fd220`. A preceding memcpy also copies 272 bytes into destination `+10`;
native memcpy and all other calls remain unexecuted.

| FDE direct caller | Destination argument provenance | Remaining uncertainty |
| --- | --- | --- |
| `dcc88`, call `dccb4` | Local stack base; source is local stack base +270 | Cannot establish the runtime key field |
| `dcd60`, call `dcd88` | This caller's argument 1; source is argument 0 | Other incoming aliases unresolved |
| `dcf0c`, call `dcf3c` | This caller's argument 1; source is argument 0 | Incoming aliases unresolved |

The known call `dcd40` from `dcc88` also supplies its stack base as `dcd60`'s
destination. That particular chain copies a stack object. Neither the other
callers nor possible indirect calls are classified as disjoint from the global
runtime context. The separate caller after the copy at `dd1dc` is likewise
unexecuted. These limits prevent declaring the candidate universally unrelated
to the key.

## Verification and inputs

**31 exact instruction checks**, four complete FDE span hashes and a bounded
inventory of direct `b`/`bl` callers pass. The inventory scans **2,040 FDE
functions / 169,679 instructions**. Four negative checks reject an altered
store, source load, stack-destination instruction or FDE boundary.

This is **static analysis with zero guest/native/JNI/Android execution**.
No protected VM record, initializer, asset transform, network or station runs.
Public results contain addresses, expressions and hashes, without raw vendor
code, SDK seeds, runtime key bytes or user data.

The input pins are unchanged:

| Input | SHA-256 |
| --- | --- |
| APK | `27986f94a992f3aac1189be746d310553e99909ed81dfbb1abd5880f71662110` |
| Packed `libexec.so` | `4fd13d12d64e6d451f5b567c173917225c1b1e2deb97abdeca10110b8dc4f379` |
| Original virtual image | `b4c434ffec580a371ece4f7ddc2f410f40d49ce73c3c2e09158194e1bd15dd57` |
| Retained initialized image, hash-checked only | `14c73475f02e708a07d982b65d5fcea65792c8d477ba293ca95187f461e6e06f` |

```sh
python3 tools/firmware_analysis/inspect_android_bulk_copy_context.py \
  --base-apk /private/path/base.apk --images-dir /private/path/images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /private/output/bulk-copy-context
```

Compare complete `android-bulk-copy-context-{results,manifest}.json` files
against `tools/firmware_analysis/expected_results/`. Independent runs reproduce
both artifacts using Python **3.14.4** and Capstone **5.0.7**.

## Remaining key and charging-pause work

The writer/value at **runtime `context+160`**, actual asset selection and
plaintext SDK DEX remain unresolved. The retained `ijiami.dat` still has a
type-3 header; the separate type-2 inflate route is not established for it.
This copy candidate supplies no charging-pause encoder.

Follow an established context constructor, protected VM store, or an incoming
alias to `dcd60`/`dcf0c` before treating this bulk copy as key initialization.
An observed pointer clear or matching immediate offset is insufficient.
