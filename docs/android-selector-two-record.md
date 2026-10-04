# Protected loader selector-2 record

## New static boundary

The [archive investigation](android-archive-callback.md) pointed to wrapper
`43308`. Its selector **2** selects descriptor `10c440`: offset **3124**,
length **410**, record start **`10c294`**, SHA-256
`556013fd0bef4072440062da80d14c2d782e5d6ec66691687107f0c2251477ba`.
The record encodes three initial frame words, consistent with the wrapper's
pointer and low-byte inputs. All **406 body bytes** now have static token
coverage: **127 states / 127 token offsets**, no unknown handlers and a single
void terminal at offset **409**.

Both conditional edges remain symbolic. No runtime memory, VM stack, malloc,
guest/native instruction, initializer, JNI or Android component executes.
Coverage is not proof of successful allocation or runtime branch selection.

## Context access and allocations

Original ELF relocation resolves native-table slot **28** to global context
cell **`fd220`**. The first token sequence loads that cell, computes
**`context+128` hexadecimal**, and loads a pointer from it. This differs from
the unresolved `context+160` transform-key field.

Native-table slot **32** is an unresolved ELF import named **`malloc`**.
Three static call sites select it through thunks **41/42/43**, at record offsets
**118/210/329**. Their complete FDE spans are `e2bac..e2c20`, `e2c20..e2c94`
and `e2c94..e2d08`; each contains one indirect native call. Original relocations
and complete span hashes are retained. The import remains uncalled.

A newly audited protector handler **`67`**, `c163c..c1700`, adds two 32-bit
words, stores the result and advances one token byte. Ten exact semantic
instruction checks plus its complete span hash establish the width/operation.
Inherited checks cover the other handlers and selector wrapper; **984 exact
instruction checks** pass in total. These handler numbers are unrelated to
station protocol opcodes.

## What remains open

This closes the previously unparsed selector-2 record boundary, not the
charging-pause encoder. Runtime allocation results, nested pointer provenance,
the writer/value of `context+160`, actual asset selection and plaintext SDK
DEX remain unresolved. The record has indirect stores; a different initial
context offset alone does not prove that no alias can reach the key field.
Further work needs bounded alias analysis or a specific audited writer path.
No seed, rolling key, raw bytecode or recovered credential is published.

The [static dataflow follow-up](android-selector-two-dataflow.md) now inventories
both paths' stores and establishes a direct `context+128` writer plus pointer
array bookkeeping. Runtime aliases and the `+160` key writer remain unresolved.

## Reproduction

```sh
python3 tools/firmware_analysis/inspect_android_selector_two.py \
  --base-apk /private/path/base.apk --images-dir /private/path/images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --output-dir /private/output/selector-two
```

Compare the `android-selector-two-{results,manifest}.json` pair in
`tools/firmware_analysis/expected_results/`. The APK, packed library and both
images use the same pins as the archive proof. Python **3.14.4**, Capstone
**5.0.7**; guest instructions **0**. Altered record, descriptor, handler code
and dispatch-pointer inputs are independently rejected.
