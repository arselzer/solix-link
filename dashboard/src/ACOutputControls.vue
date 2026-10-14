<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Proposal, Station } from './types';
const props = defineProps<{ station: Station; active: boolean }>();
const emit = defineEmits<{ propose: [proposal: Proposal] }>();
const hold = ref(20);
const power = computed(() => props.station.ac_power_control);
const unlocked = computed(() => props.active && power.value?.enabled === true
  && power.value.storage_valid === true && !power.value.restore_pending
  && props.station.metrics.software_version === '2.1.6.4');
function permission() {
  if (!props.active || !power.value?.manageable) return;
  const enabled = !power.value.enabled;
  emit('propose', { station: props.station.name, permissionChange: true, highImpact: enabled,
    body: { command: 'set-ac-output-permission', enabled, acknowledge_high_impact: enabled },
    title: enabled ? 'Allow AC output changes?' : 'Lock AC output changes?',
    detail: 'This changes the local permission only. AC OFF and power cycles interrupt every attached AC load. An already armed cycle will still restore ON.',
    summary: [enabled ? 'Enable permission for this station' : 'Disable new AC changes', 'No output command is sent'] });
}
function output(enabled: boolean) {
  if (!unlocked.value || !props.station.controls.includes('set-ac-output')) return;
  emit('propose', { station: props.station.name, highImpact: !enabled,
    body: { command: 'set-ac-output', enabled, acknowledge_power_interruption: !enabled },
    title: enabled ? 'Turn AC output ON?' : 'Turn every AC socket OFF?',
    detail: enabled ? 'Restore power to every AC socket.' : 'All attached AC loads lose power and remain off until an explicit ON request.',
    summary: [enabled ? 'AC output: ON' : 'AC output: OFF', 'DC output and charging settings are preserved'] });
}
function cycle() {
  if (!unlocked.value || !props.station.controls.includes('cycle-ac-output')
      || !Number.isInteger(hold.value) || hold.value < 5 || hold.value > 60) return;
  emit('propose', { station: props.station.name, highImpact: true, independentController: true,
    body: { command: 'cycle-ac-output', off_seconds: hold.value,
      acknowledge_power_interruption: true, controller_independent: true },
    title: 'Power-cycle every AC load?',
    detail: 'The controller, access point and router must stay powered independently. The worker saves an ON restoration intent before OFF; hardware or network failure can still delay restoration.',
    summary: [`Hold OFF for ${hold.value} seconds after confirmation`, 'Restore ON, with bounded ON retries', 'Never retry OFF automatically'] });
}
</script>

<template>
  <section v-if="station.model === 'c2000_gen2' && station.protocol === 'native_mqtt' && power?.supported" class="panel" data-testid="ac-output-controls">
    <h2>AC output</h2>
    <p>Changes affect every AC socket. Permission starts locked and must be enabled separately for this station.</p>
    <p v-if="!power.storage_valid" class="validation-error">Permission storage needs operator review. AC changes are blocked.</p>
    <p v-if="power.restore_pending" class="validation-error">ON restoration is pending. Check fresh status before another interruption.</p>
    <button v-if="power.manageable" class="secondary" data-testid="ac-permission" :disabled="!active || !power.storage_valid" @click="permission">{{ power.enabled ? 'Lock AC output changes' : 'Allow AC output changes' }}</button>
    <div class="confirmation-actions">
      <button class="secondary" data-testid="ac-on" :disabled="!unlocked || !station.controls.includes('set-ac-output')" @click="output(true)">Turn AC ON</button>
      <button class="secondary" data-testid="ac-off" :disabled="!unlocked || !station.controls.includes('set-ac-output')" @click="output(false)">Turn AC OFF</button>
    </div>
    <label>OFF hold (seconds) <input v-model.number="hold" type="number" min="5" max="60" data-testid="ac-cycle-hold"></label>
    <button class="secondary" data-testid="ac-cycle" :disabled="!unlocked || !station.controls.includes('cycle-ac-output') || !Number.isInteger(hold) || hold < 5 || hold > 60" @click="cycle">Review AC power cycle</button>
  </section>
</template>
