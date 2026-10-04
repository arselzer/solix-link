# Selector-2 loader bookkeeping and indirect-store limits

## Static evidence

This follows the [410-byte record audit](android-selector-two-record.md) for
the same hash-pinned SDK build. It models static operand widths and local
copies across both branches, with **zero guest/native/JNI execution**.
Dereferences and allocation results remain expressions; no runtime memory or
malloc operation is evaluated.

Seven additional instruction checks establish that the word and byte local
views share a frame base, while the high pointer-word view begins at base +4.
Twelve checks on each of three already hash-bound thunks establish their
two-pointer-input/one-pointer-return stack layout. These **43 checks** supplement
the preceding **984** checks. Each path ends with an empty operand stack.
Four negative cases reject altered frame views, native return instructions,
local byte aliases and indirect-store widths.

## What the record writes

The only conditional tests whether the pointer loaded from `context+0x128`
is null. Static expressions retain both outcomes:

| Path | Steps | Allocation size arguments | Indirect stores |
| --- | ---: | --- | ---: |
| Existing pointer | 78 | 96 | 3 |
| Null pointer | 113 | 96, 2040, 96 | 7 |

The null branch stores an allocation result directly at **`context+0x128`**.
It writes `255` at the new object's `+0x38`, zero at `+0x48`, and an allocation
result through the reloaded object's `+0x58`. Both branches then:

1. Load the 32-bit value at object `+0x48`, add one with word arithmetic and
   write it back.
2. Store a newly allocated pointer at an array address plus
   `8 * sign_extend(original_word_value)`.
3. Store the wrapper's original input pointer at the new allocation's `+0x28`.

This supports a **loader record-list bookkeeping interpretation**. The 2040-byte
argument equals 255 pointer-sized entries, but allocation success, capacity
enforcement and the running app's object invariants are not proved. The
forwarded low-byte argument is not read by this record's traced local loads.

## Remaining boundary

No direct store target in these expressions is `context+0x160`. That does not
prove absence of a key write: loaded pointers, array addresses and allocation
results remain symbolic and could alias runtime memory. Stores are inventoried,
not propagated into later loads; the analysis assumes neither allocation
success nor disjoint objects, and native side effects are not evaluated.

The exact **`+0x160` writer/value, selected asset and plaintext SDK** remain open.
This is not a charging-pause encoder or station command. Follow a concrete
context-construction/key-writer path next; this record alone cannot resolve it.

## Reproduction

```sh
python3 tools/firmware_analysis/inspect_android_selector_two_dataflow.py \
  --base-apk /private/path/base.apk --images-dir /private/path/images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /private/output/selector-two-dataflow
```

Compare `android-selector-two-dataflow-{results,manifest}.json` with
`tools/firmware_analysis/expected_results/`. Python **3.14.4**, Capstone **5.0.7**;
the manifest pins the APK, packed/original/initialized images and local sources.
Public output includes structural expressions, addresses and hashes, with no
raw record, seed, rolling key, runtime identity or recovered key.
