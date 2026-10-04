# C1000 Gen 2 complete saved-backup file and reference audit

## Result and scope

Offline audit dated 2026-10-02 of **A1763 C1000 Gen 2 main 1.1.4.9**. The
controller's **415-byte `sysPara` file contains the complete four saved
disaster-preparation records, their maximum values and both raw switches**.
[28 synthetic instruction cases](../tools/firmware_analysis/emulate_gen2_syspara_backup.py)
execute its entire load/save routines, checksum and settings validation. This
provides an exact artifact format for future independent backup work.

**No external full-file or full-record export command is established.** Existing
ordinary D9 status remains incomplete. A filename, internal file operation or
factory reload is not a readback protocol, and this audit does not unlock backup
setters. No station, Bluetooth, MQTT, SSH, cloud, actual filesystem, configuration
or electrical output was accessed. C2000 equivalence is unproved.

## Complete `sysPara` layout

The complete in-memory image begins at `20001d48`. Loader `080288c8` reads
`019f` hex =415 bytes into that address; saver `0802c994` writes the same size.
The filename pointer comes from the initialized array at `200000e4`, member
`+4`. Actual RAM initialization was inspected separately: this pointer resolves
to public string `sysPara` at `08034dec`. Other members name `touPara`,
`trackingPara` and `settingStats`; they are separate files.

| File offset | Size | Meaning |
| --- | ---: | --- |
| `0000` | 4 | ASCII magic `sysP` |
| `0004` | 1 | CRC-8 of bytes `[5:415]` |
| `0005` | 410 | Complete settings body, including the backup block |
| `0161` | 9 | Automatic record 0 |
| `016a` | 9 | Automatic record 1 |
| `0173` | 9 | Automatic record 2 |
| `017c` | 9 | Manual record |
| `0185` | 1 | Raw manual-enable switch |
| `0186` | 1 | Raw automatic-enable switch |

Each record is `maximum_u8, start_u32_le, end_u32_le`, equivalent to Python
`struct.unpack('<BII', nine_bytes)`. There is no supplied minimum-SOC byte or
wire type byte in this stored record. The complete backup region is therefore
`file[0x161:0x187]`, 38 bytes, corresponding to RAM `20001ea9..20001ece`.
The existing [writer audit](gen2-disaster-plan-investigation.md#native-005e-fields)
documents the different typed request format and cancellation side effects.

Actual checksum instructions `0800a01c..0800a06e` implement CRC-8 polynomial
`07`, initial value zero, MSB first and no final XOR. The replay independently
computes it in host Python and compares the actual instruction result. This
checks integrity of the captured body; CRC does not identify the station, verify
freshness, authorize a restore or validate every record's meaning.

## Persistence and defaults are observable instructions

| Executed path | Result with synthetic file I/O |
| --- | --- |
| Save `0802c994` with stale checksum | Updates magic/checksum, opens `sysPara`, writes 415 bytes, syncs, rereads and compares the whole file |
| Load `080288c8` with a valid complete file | Reads all 415 bytes, checks size/magic/CRC and actual settings validator; preserves all records and raw switches exactly |
| Short file, bad magic or wrong checksum | Calls actual default initializer `08029aa0`; zeros all 38 backup bytes |
| Complete file with valid CRC but zero charging power | Validator rejects it, then the same defaults erase the backup block |

Twelve save/load pairs vary two distinct four-record fixtures and six switch
pairs: `0/0`, `1/0`, `0/1`, `1/1`, `2/255`, `255/2`. Valid file loading retains
even the noncanonical raw switch bytes. The ordinary switch getter normalizes
nonzero to true, and the ordinary writer normalizes nonzero to one; these are
separate interfaces. A backup that preserves only booleans can lose raw state.

The four rejected-file cases demonstrate **destructive default recovery**, not
a reversible query. They complement the earlier [invalid charging-power load
finding](c1000-charging-control-followup.md). No flash commit or real boot was
tested, and valid backup records were not selected or allowed to affect charging
in these cases. Other fields outside the backup block also exist; retain the
entire artifact privately rather than treating these 38 bytes as a replacement
for arbitrary settings or identity data.

## Complete-record and switch references

The new hash-bound scan examines every halfword start for direct `BL`, `BLX`,
`B` and `B.W` candidates to both getters, and every byte start for Thumb pointer
literals. It finds **19 direct record-getter callers and five switch-getter
callers**, all `BL`; no tail branch or literal function pointer to either getter
was found. The expected JSON also records all **74 PC-relative literal-reference
candidates** to the settings base or an address within the backup block.

| Call sites | Data flow |
| --- | --- |
| Record getter `0801a608`: `08009420`, `08009440`, `08009480` | Cancellation/window invalidation |
| Record getter: 14 sites `08018afc..08018c4e` | Active selection and cached-selection validation |
| Record getter: `08019184`, `08019192` | D9 copies manual start/end; excludes its maximum and every automatic record |
| Switch getter `0801a61c`: `08018b32`, `08018b56`, `08018bee` | Active selector checks manual/automatic enable |
| Switch getter: `08019170`, `0801917a` | D9 exports normalized manual/automatic switch state |

The direct backup-address literal candidates resolve as follows:

| Instruction sites | Pointer / operation |
| --- | --- |
| `080093e0` | `20001ea9`, destination for clearing 38 bytes |
| `080094e8` | `20001ec4`, destination for manual-record storage |
| `0801a60c`, `0801a610` | Manual pointer, or subtracts 27 for the three-record automatic array |
| `0802b43e`, `0802b460` | `20001ea9`, automatic-record copy/clear destinations |
| `0802da66`, `0802da78`, `0802daac` | Literal `20001ec8`, followed by `+20` hex: accesses separate region `20001ee8`, not the backup block |

That last apparent overlap was a new lead worth resolving. The literal points
inside the manual record, but these instructions use an offset past the complete
settings file. Its persistence routine is for `touPara`; it is not a hidden
backup serializer. Its other comparison at `0802dae2` reads only the first
96 bytes from the settings base, also excluding the backup block.

## Filename array, factory and radio limits

Separate static inspection found these **14 PC-relative references** to the
filename-array base `200000e4`:

| Sites | Reviewed role |
| --- | --- |
| `080288cc`, `0802c9be` | `sysPara` load/save, member `+4` |
| `080289c4`, `0802da8c` | `touPara` load/save, member `+8` |
| `08028aac`, `0802de9c` | Tracking-file load/save, member `+12` |
| `0802869a`, `0802be68` | Settings-statistics file, member `+16` |
| `08015c56`, `080238f6`, `0802c450` | Persistence timer state at array-prefix byte `+1` |
| `08019428`, `0802ad28` | Variant byte at prefix `+0` |
| `0802ac22` | Stores an unrelated member at `+2e` |

Direct calls to full loader `080288c8` come from `08017846`, inside startup helper
`08017840`; its reviewed direct caller is `0800700e`. Full saver `0802c994` has
five direct callers: `080160f0`, `0802b338`, `0802b574`, `0802b58c`, `0802b5a4`.
The recovered 51-entry factory table and 17-entry ordinary app table do not
directly register these full-file routines. This does not prove that no higher
wrapper, indirect call or generic filesystem service exists. The firmware's
FTP-named chunk routines do not establish a request accepting this filename;
no raw filename query or factory probe is proposed.

The subsequent [asset-transfer startup replay](gen2-asset-transfer-export-limits.md)
executes one FTP-named route and its initial descriptor/serializer. It queues
a fixed request without reading the pointed-to contents or saved settings,
and changes transfer/timer state. Its thirty-case reply/timeout continuation
follows the supplied locator into an outbound radio `003d` request without
reading controller settings. Later [radio admission](radio-asset-download.md)
and [buffer/controller replays](gen2-asset-chunk-buffer-contract.md) follow
HTTP asset download and MAIN chunk forwarding. A complete generic file-export
route remains unestablished; no saved-file producer is supplied by those paths.

The public radio **0.3.3.0** image has no discovered literal `sysPara`, `sysP`,
disaster or storm string in its mapped printable data. This is only a string
search, not absence of support. Its named device-parameter/MQTT-file paths and
the app's BackupMind/cloud schemas were already traced in the [radio/app
audit](gen2-backup-export-radio-app.md); that negative result is unchanged.
Neither a radio connection-file backup nor diagnostic tracking report can
substitute for these four stored records.

## What would unlock a reversible setter

An established external producer must return **all 38 stored bytes or their
lossless equivalent**, with freshness and switches demonstrated. Alternatively,
an independently acquired complete `sysPara` artifact could establish a backup,
but no service, debug connector or physical extraction route was tested here.
Flash-file state can lag RAM; even a correct file is not automatically a fresh
controller baseline. Active-cache/status still needs separate fresh observation.

Any restore must preserve dormant records as well as active ones, handle raw
switches honestly, and avoid cancellation or defaults that erase other windows.
Factory reload, clear-all, altered checksum or invalid settings are unsuitable
ways to obtain a baseline. Keep backup setters gated until this prerequisite is
met; leave C2000 server outputs untouched.

## Reproduction and boundaries

```sh
SOLIX_ANALYSIS_OUTPUT=/tmp/gen2-syspara-backup \
  python3 tools/firmware_analysis/emulate_gen2_syspara_backup.py
cmp /tmp/gen2-syspara-backup/gen2-syspara-backup-results.json \
  tools/firmware_analysis/expected_results/gen2-syspara-backup-results.json
cmp /tmp/gen2-syspara-backup/gen2-syspara-backup-manifest.json \
  tools/firmware_analysis/expected_results/gen2-syspara-backup-manifest.json
```

Use the published analysis dependencies. Main image: 198656 bytes, loaded at
`08005000`, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.
`SOLIX_FIRMWARE_DIR` can supply the exact image externally. The separate string
inspection uses radio hash
`e291ec115f013953e825cb51b9e457a8731889547ab55b3058640e599e8cfec8`.

Cases: 12 full saves, 12 full loads, four rejected-file/default sequences. Result
SHA-256: `fb74eb335ea64c70821e07ee5c6ec7230581a456fd981029fdb7b354d05f623d`.
An independent parent replay produced the same result. Public JSON contains
synthetic records, switches, hashes, constants and file-call summaries only.
Raw search/disassembly evidence remains private.

ROM is read/execute-only and instruction writes are restricted to synthetic
RAM. Actual file I/O, libc, logging, deferred persistence scheduling and a
product-default option are substitutes. Actual flash, file freshness, external
export/restore protocols, app/radio execution and device/charging behavior are
excluded. The candidate scan covers the stated encodings; computed pointers,
compressed initialized aliases, arbitrary data flow and code/data boundaries
remain limits rather than a whole-program absence proof.
