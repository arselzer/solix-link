# Factory aggregate status is not a saved-settings export

## Executed scope

The previous [diagnostic audit](gen2-diagnostic-getter-audit.md) left factory
selector **1**, property **0016**, unexecuted. Its callback **`0802eec4`** now
passes **seven synthetic cases** on public A1763 main **1.1.4.9**, SHA-256
`21ffb746c1e07ecaa9817fa7017807585a00bedbca3f136c650129bb52a4a0c9`.

Actual factory parser/dispatcher, aggregate/getters, serializer and CRC
instructions run within a 50,000-instruction bound. Memory/string helpers,
logger and RX/TX ring boundaries remain explicit inherited substitutes.
Synthetic RAM/GPIO are used; ROM/GPIO are read-only. No physical transport,
whole firmware, filesystem backend, station or external query executes.

## Result and side effects

The callback produces **ten data bytes / 21 framed reply bytes** from cached
status predicates. It does not read any of the **38 saved backup bytes**.
All cases preserve the complete **415-byte settings block** and protected
eight-byte output-state region. Three additional pairs alter different saved
record bytes but retain identical complete replies, providing independent
counterexamples to complete export through this property.

The route is **not passive**:

- The enclosing factory dispatcher stops an allocated upgrade timer, as for
  the previously examined factory getters. Its state changes from 2 to 3.
- Getter `0801aab8` refreshes runtime cache; the executed cases write byte
  **`20000228`**. It is not a saved configuration field or battery-energy unit.

Two synthetic seeds with and without an allocated timer cover four cases;
the three reply-collision pairs make seven. Public results retain hashes,
read/write metadata and preservation assertions, not raw identities or replies.
Other BMS predicate branches and factory properties remain unexecuted.

## Consequence for integration

This removes one indirect aggregate candidate. It supplies no complete
backup, read-only local API, new HA sensor or justification for a factory probe.
Physical USB access remains unverified; the Gen 2 radio diagnostic table still
has no established installer. Firmware support for a factory callback does not
establish its accessibility over Bluetooth or native MQTT.

Continue with a concrete ordinary/radio producer or generic file-export path
that can prove full readback. The internally complete `sysPara` artifact and
ordinary-status collision findings remain described in
[the file audit](gen2-syspara-backup-format.md) and
[all 19 status callbacks](gen2-full-status-inventory.md).

The later [asset-transfer startup replay](gen2-asset-transfer-export-limits.md)
examines another indirect file-service candidate. Its first request is fixed
and its transfer/timer side effects likewise exclude a passive backup query;
the later worker remains a bounded lead.

## Reproduction

```sh
SOLIX_ANALYSIS_OUTPUT=/private/output/factory-aggregate \
python3 tools/firmware_analysis/emulate_gen2_factory_aggregate.py \
  --output-dir /private/output/factory-aggregate
```

Compare `gen2-factory-aggregate-{results,manifest}.json` with `expected_results/`.
Manifest: Python **3.14.4**, Unicorn **2.1.4**, exact image and local source hashes.
