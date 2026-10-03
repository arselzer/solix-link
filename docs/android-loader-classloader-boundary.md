# Native ClassLoader buffers and the protected container boundary

## Result and limits

The real `al` entry discovered in [record 2](android-loader-record-two.md)
contains a call into a loader dispatcher at `0007a6e8`. Two dispatcher
targets prepare Java `ByteBuffer[]` objects from the same runtime native
record list. One supplies the exact multiple-buffer
`InMemoryDexClassLoader` constructor signature and original parent
ClassLoader to an unresolved native JNI helper.

This is a concrete static buffer boundary. It is **not** recovered SDK DEX
or demonstrated running-app behavior. No native method, Android/JNI call,
protector interpreter, initializer, cloud request or station command ran.
The record data, key provenance and helper implementations remain open.
No AC-input-disable or charging-pause packet follows from this result.

## Pinned inputs and reproduction

The APK and both private `libexec` snapshots are the exact inputs pinned by
the [record-two tool](android-loader-record-two.md). This tool reads the
earlier initialized snapshot only for twelve fixed class/signature/format
literals; it does not rerun initializers. Code and record analysis use the
original carrier snapshot with pure ELF RELATIVE relocation parsing.

The additional primary source is a local OpenJDK 21 `jni.h`, SHA-256
`99e64ebbe749e6df284f852f11b3c73f6ea97baf15120428f40f887fe0616e61`.
Its actual `JNINativeInterface_` declaration establishes selected 64-bit
table offsets; the tool requires this exact header rather than silently
substituting another layout.

```sh
python3 tools/firmware_analysis/inspect_android_loader_classloader.py \
  --base-apk /private/path/base.apk \
  --images-dir /private/path/carrier-virtual-images \
  --initialized-image /private/path/libexec.so.decoded-strings-memory.bin \
  --jni-header /usr/lib/jvm/java-21-openjdk-amd64/include/jni.h \
  --output-dir /tmp/android-loader-classloader
```

Compare complete `android-loader-classloader-{results,manifest}.json` with
the corresponding files in `tools/firmware_analysis/expected_results/`.
Addresses below are virtual-image offsets; field/table offsets are
hexadecimal, and ranges end exclusively.

## Dispatcher and record preparation

At `000778e4`, `al` prepares original JNIEnv, original parent ClassLoader,
zero, and the fourth dispatcher argument before calling `0007a6e8`.
The Application registration candidate `l` contains another call to this
dispatcher at `00076784`. These are static call sites, not proven active
app paths.

The dispatcher first checks runtime context field `+128`. Its zero branch
calls `000b20c4`. Later branches compare context integer `+d4` against 28
and 25 and inspect flags, selecting `000af374`, `000b1db4`, `000aee74`, or
older per-record paths. This trace does not establish the source of that
integer or which strategy the running app selects.

`000b20c4..000b20fc` initializes interpreter context, then tail-calls the
same descriptor selector used by record 2, with these distinct parameters:

| Parameter | Value |
| --- | --- |
| Native call table | `00118a40` |
| Native thunk table | `00118ac0` |
| Protected record base | `00118b30` |
| Descriptor table | `0011919c` |
| Selected record index | `0` |
| Stored offset / length | `0` / `1644` |
| Record SHA-256 | `11889935baf88763cb254647ed77bd71f343b12714e578d6d007982b8c6dae1e` |

The new record is not the earlier 182-byte JNI record. Its header starts
with zero frame words, followed by handler `2d` reserving 334 words.
The tool statically parses **27 tokens**, covering body bytes `4..83`, then
stops at the first branch. Three new native width checks establish signed
word-count reservation (`2d`, two operand bytes), immediate byte (`30`, one),
and indirect byte load (`42`, zero).

The prefix dereferences call-table entry 0, the runtime-context pointer slot
`000fd220`, and tests byte `context+370` against zero. Branch `86` at record
offset 79 has signed displacement 19: its unknown comparison result selects
offset 98 or 83. Neither successor is decoded in this batch; the remaining
1,561 bytes must not be treated as a decoded or executed program.

## Java buffer construction

Both strategies use this symbolic native-record path:

```text
context + 0x128 -> pointer + 0x58 -> record[i] + 0x28 -> data
uint32(data + 0x20) -> length
```

The length offset resembles a DEX `file_size` field, but this trace does not
validate magic, bounds, checksums, the record count, or plaintext contents.

### Byte-array strategy: `000af374..000af5f8`

At `000af3e8`, the code prepares `NewObjectArray` for the record count and
the selected `java/nio/ByteBuffer` class. Each record then prepares:

1. `NewByteArray(length)` at `000af4c0` (JNI slot `580`).
2. `SetByteArrayRegion(array, 0, length, data)` at `000af4e4` (slot `680`).
3. Context helper `+20/+80` at `000af514`, with class, method name and
   signature for `ByteBuffer.wrap(byte[], int, int)`.
4. `SetObjectArrayElement` at `000af530` (slot `570`).

At `000af590`, context helper `+20/+b0` receives class literal
`dalvik/system/InMemoryDexClassLoader`, signature
`([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V`, the buffer array and the
original parent ClassLoader. Its implementation is not resolved here.
If the returned object is non-null, the code prepares `NewGlobalRef` and
saves its result at runtime context `+1e8`.

### Direct-buffer strategy: `000aee74..000af374`

At `000aefc0`, this strategy prepares JNI `NewDirectByteBuffer` (slot `728`)
from the same data pointer and `uint32(data+20)` length, then stores each
buffer in a Java array. A later context-helper call is prepared with
`dalvik/system/DexPathList.makeInMemoryDexElements` and signature
`([Ljava/nio/ByteBuffer;Ljava/util/List;)[Ldalvik/system/DexPathList$Element;`.
It does not copy or validate the native source in the observed preparation.

These JNI slot meanings are checked against the pinned header. Context
helper meanings remain argument-based candidates until their implementations
and initialization are traced.

## Candidate asset transform, kept separate

Native helper `000b6b34..000b6c74` is a distinct promising container path,
reachable from a selected native helper table candidate. Its caller through
the new protected record has not been decoded yet.

It calls `000b69e8` to obtain an asset buffer. That reader formats either
`assets/%s` or `%s` using runtime descriptor field `+28`, then calls one of
runtime context `+48` interfaces. Their actual raw-reader targets and the
filename field remain unresolved here.

The native helper skips a **44-byte header**, calls `000b6d44` on the payload,
allocates output according to header word `+08`, then calls `00092104` with
compressed-length word `+04`. The latter has an inflate-style init/run/end
structure; it is not executed or used as a standalone decoder.

The in-place transform derives 26 key bytes by XORing selected constant
bytes with bytes beginning at `runtime_context+160` plus three. Its key
source is unknown. This is separate from the earlier `DETool.dowork`
preferences/database route. No container was decrypted or inflated here.

## Verification and next bounded step

The tool checks **336 exact native instructions**, 12 full FDE-bounded
function disassemblies, twelve whitelisted literals, eight JNI table slots,
descriptor bounds and three new handler widths. The prefix is bounded to
64 tokens and stops at its exact first branch. Guest instruction count is
zero. Inputs are preflighted; metadata is written only after all checks pass.

Private failure checks cover altered APK/carrier/initialized/header inputs
and malformed static prefixes. Raw files stay ignored with 700/600
permissions. No previous tool or fixture is modified.

Next, trace both prefix successors and the native preparation/allocator
calls using individually verified handler widths. Establish the actual
asset name, runtime key provenance, decoded record allocation and first
validated DEX header before considering SDK serializer inspection. Static
ClassLoader signatures alone cannot establish a working decoder or device
control.
