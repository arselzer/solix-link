// Exercise the bundled UI against synthetic stations. Never use a real gateway.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, readFile } from 'node:fs/promises';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../../', import.meta.url));
const fixturePath = fileURLToPath(new URL('./fixture_server.py', import.meta.url));
const listener = net.createServer();
listener.listen(0, '127.0.0.1');
await once(listener, 'listening');
const port = listener.address().port;
await new Promise((resolve) => listener.close(resolve));
const base = `http://127.0.0.1:${port}`;
const authorization = 'Bearer demo-token-not-secret';
const python = process.env.SOLIX_TEST_PYTHON || 'python3';
const fixture = spawn(python, [fixturePath], {
  env: { ...process.env, PYTHONPATH: `${root}/python`, SOLIX_TEST_PORT: String(port) },
  stdio: ['ignore', 'ignore', 'inherit'],
});
let browser;
let cases = 0;
const errors = [];
const external = [];
async function recorded() {
  const response = await fetch(`${base}/fixture`, { headers: { Authorization: authorization } });
  const result = await response.json();
  assert.equal(result.synthetic_fixture, true);
  return result.commands;
}
async function change(values) {
  const response = await fetch(`${base}/fixture`, { method: 'POST', headers: { Authorization: authorization, 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
  assert.equal(response.status, 200);
}
try {
  const deadline = Date.now() + 10000;
  while (true) {
    try { await recorded(); break; }
    catch {
      if (fixture.exitCode !== null || Date.now() > deadline) throw new Error('Synthetic fixture failed to start');
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  browser = await chromium.launch({ headless: true, ...(process.env.SOLIX_CHROMIUM_PATH ? { executablePath: process.env.SOLIX_CHROMIUM_PATH } : {}), args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  let simulatedNow = Date.now();
  await page.clock.install({ time: simulatedNow });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error' && message.text().includes('Content Security Policy')) errors.push(message.text()); });
  let posts = 0;
  page.on('request', (request) => {
    if (!request.url().startsWith(base + '/')) external.push(request.url());
    if (request.method() === 'POST' && request.url().includes('/commands')) posts++;
  });
  await page.goto(base);
  const token = page.getByTestId('token-input');
  assert.equal(await token.getAttribute('type'), 'password');
  await token.fill('wrong-token');
  await page.getByTestId('connect').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Access denied' }).waitFor();
  assert.equal(await token.inputValue(), '');
  assert.equal(await recorded().then((calls) => calls.length), 0);
  cases++; console.log(`Scenario ${cases} passed`);

  async function connect() {
    await page.getByTestId('token-input').fill('demo-token-not-secret');
    await page.getByTestId('connect').click();
    await page.getByText('Live telemetry', { exact: true }).waitFor();
  }
  async function refresh() {
    await page.getByTestId('refresh').click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="refresh"]')?.disabled);
  }
  async function propose(label) {
    const setting = page.locator('.setting').filter({ has: page.locator(`label[for="${label}"]`) });
    await setting.getByRole('button', { name: 'Apply', exact: true }).click();
    await page.getByTestId('command-review').waitFor();
  }
  await connect();
  await page.getByTestId('reported-telemetry').locator('summary').click();
  assert.ok((await page.getByTestId('telemetry-time_remaining_minutes').textContent()).includes('Time to empty (estimate)'));
  assert.ok((await page.getByTestId('telemetry-time_remaining_minutes').textContent()).includes('120 min'));
  assert.ok((await page.getByTestId('telemetry-software_version_module').textContent()).includes('0.3.3.0'));
  assert.equal(await page.getByTestId('telemetry-dc_output_timer_remaining_seconds').count(), 0);
  await page.getByTestId('station-select').selectOption('Spare · C1000');
  await page.getByTestId('reported-telemetry').locator('summary').click();
  assert.ok((await page.getByTestId('telemetry-usb_c1_power_w').textContent()).includes('15 W'));
  assert.ok((await page.getByTestId('telemetry-usb_a2_power_w').textContent()).includes('0 W'));
  assert.ok((await page.getByTestId('telemetry-time_remaining_minutes').textContent()).includes('Unavailable'));
  assert.equal(await page.getByTestId('telemetry-software_version_module').count(), 0);
  await page.screenshot({ path: `${root}/docs/images/web-dashboard-reported-telemetry.png`, fullPage: true });
  await page.getByTestId('station-select').selectOption('Server · C2000 Gen 2');
  await page.getByTestId('reported-telemetry').locator('summary').click();
  assert.ok((await page.getByTestId('telemetry-dc_output_timer_remaining_seconds').textContent()).includes('No active countdown'));
  assert.ok((await page.getByTestId('telemetry-software_version_bms').textContent()).includes('3.4.5.6'));
  assert.equal(await page.getByTestId('telemetry-usb_a1_power_w').count(), 0);
  assert.deepEqual(await page.locator('#display-timeout option').evaluateAll((items) => items.map((item) => item.value)), ['30', '60']);
  assert.ok((await page.locator('.setting').filter({ has: page.locator('#display-timeout') }).textContent()).includes('awaits a device test'));
  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  const wifiReport = { schema_version: 1, source: 'radio_ap_info', main_version: '1.1.4.9', radio_version: '0.3.3.0',
    settings_unchanged: true, observed_at: simulatedNow / 1000 - 1, wifi_rssi_dbm: -42 };
  await change({ wifi_signal: { 'Office · C1000 Gen 2': wifiReport } });
  await refresh();
  await page.getByTestId('reported-telemetry').locator('summary').click();
  assert.ok((await page.getByTestId('telemetry-wifi_rssi_dbm').textContent()).includes('-42 dBm'));
  await change({ wifi_signal: { 'Office · C1000 Gen 2': { ...wifiReport, observed_at: simulatedNow / 1000 - 601 } } });
  await refresh();
  assert.ok((await page.getByTestId('telemetry-wifi_rssi_dbm').textContent()).includes('Unavailable'));
  await change({ wifi_signal: {} });
  await refresh();
  assert.equal(await page.getByTestId('telemetry-wifi_rssi_dbm').count(), 0);
  cases++; console.log(`Scenario ${cases} passed`);
  const originalRaw = { schema_version: 1, protobuf_name: 'charging_pps_series_c_0002', units_verified: false,
    layout_provenance: 'main_1_5_9_encoder', firmware_version: '1.7.1', reported_at: simulatedNow / 1000,
    reports: [{ counters: Object.fromEntries(Array.from({ length: 8 }, (_, i) => [`counter_${i + 1}_raw`, (i + 1) * 123])), event_timestamp: simulatedNow / 1000 }] };
  await change({ overrides: { 'Office · C1000 Gen 2': {}, 'Server · C2000 Gen 2': {}, 'Spare · C1000': {}, 'Updated · C1000': {},
    'Local · C1000': { expansion_battery_count: 1, expansion_battery_percentage: 75, expansion_temperature_c: 25 } },
    original_counters: { 'Local · C1000': originalRaw } });
  await page.getByTestId('station-select').selectOption('Local · C1000');
  await refresh();
  await page.getByTestId('reported-telemetry').locator('summary').click();
  assert.ok((await page.getByTestId('telemetry-expansion_battery_percentage').textContent()).includes('75 %'));
  assert.ok((await page.getByTestId('telemetry-expansion_temperature_c').textContent()).includes('25 °C'));
  assert.ok((await page.getByTestId('telemetry-original_counter_1_raw').textContent()).includes('123'));
  await page.screenshot({ path: `${root}/docs/images/web-dashboard-expansion-counters.png`, fullPage: true });
  await change({ original_counters: { 'Local · C1000': { ...originalRaw, reports: [{ counters: { counter_1_raw: Number.MAX_SAFE_INTEGER + 1 } }] } } });
  await refresh();
  assert.ok((await page.getByTestId('telemetry-original_counter_1_raw').textContent()).includes('Unavailable'));
  await change({ original_counters: { 'Local · C1000': { ...originalRaw, reports: [...originalRaw.reports, ...originalRaw.reports] } },
    overrides: { 'Office · C1000 Gen 2': {}, 'Server · C2000 Gen 2': {}, 'Spare · C1000': {}, 'Updated · C1000': {},
      'Local · C1000': { expansion_battery_count: 0, expansion_battery_percentage: 75, expansion_temperature_c: 25 } } });
  await refresh();
  assert.equal(await page.getByTestId('telemetry-expansion_battery_percentage').count(), 0);
  assert.equal(await page.getByTestId('telemetry-original_counter_1_raw').count(), 0);
  assert.ok((await page.getByTestId('telemetry-original_counter_batch').textContent()).includes('Batch ordering unknown'));
  await change({ overrides: { 'Office · C1000 Gen 2': {}, 'Server · C2000 Gen 2': {}, 'Spare · C1000': {}, 'Updated · C1000': {}, 'Local · C1000': {} }, original_counters: {} });
  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);
  assert.equal(await page.locator('#temperature-unit').count(), 1);
  assert.equal(await page.locator('#off-grid-alert').count(), 1);
  assert.equal(await page.locator('#device-timeout').inputValue(), '0');
  assert.match(await page.getByTestId('pv-weak-light-lock').textContent(), /Inactive/);
  assert.match(await page.getByTestId('pv-weak-light-lock').getAttribute('title'), /physical PV behavior untested/);
  assert.deepEqual(await page.evaluate(() => [localStorage.length, sessionStorage.length]), [0, 0]);
  assert.equal((await context.cookies()).length, 0);
  assert.equal(await page.getByRole('button', { name: /AC output/i }).count(), 0);
  cases++; console.log(`Scenario ${cases} passed`);

  // Fleet cards and explanations consume the same cached device response.
  const fleet = page.getByTestId('fleet-overview');
  assert.equal(await fleet.locator('.fleet-card').count(), 5);
  assert.match(await fleet.textContent(), /5\/5 with fresh telemetry/);
  assert.match(await fleet.textContent(), /Chained supplies can count the same load/);
  const initialPosts = posts;
  await fleet.locator('[data-station="Server · C2000 Gen 2"]').getByRole('button', { name: 'View station' }).click();
  const explanations = page.getByTestId('control-availability');
  await explanations.locator('summary').click();
  await explanations.getByRole('checkbox', { name: 'Include unsupported controls' }).check();
  assert.match(await explanations.locator('tr').filter({ hasText: 'set-display-timeout' }).textContent(), /Cached prerequisites met/);
  assert.match(await explanations.locator('tr').filter({ hasText: 'set-clock-brightness' }).textContent(), /Not established for this model/);
  await mkdir(`${root}/docs/images`, { recursive: true });
  await explanations.screenshot({ path: `${root}/docs/images/web-control-availability.png` });
  await fleet.screenshot({ path: `${root}/docs/images/web-fleet-overview.png` });
  await fleet.locator('[data-station="Office · C1000 Gen 2"]').getByRole('button', { name: 'View station' }).click();
  assert.equal(posts, initialPosts);
  cases++; console.log(`Scenario ${cases} passed`);

  const savedPlanPosts = posts;
  const energyNow = await page.evaluate(() => Date.now() / 1000);
  const nativeEnergy = { schema_version: 1, source: 'device_energy_report', model: 'c1000_gen2',
    firmware_version: '1.1.4.9', units_verified: false, reported_at: energyNow - 600,
    counter_epoch_started_at: energyNow - 1200, counter_epoch: 2, received_reports: 3, batch_reports: 1,
    continuity: 'counter_decreased', groups: { standard: { raw: { ac_input_energy_raw: 1250,
      ac_output_energy_raw: 1200, ac_output_duration_raw: 12 }, energy_kwh: { ac_input: 999 }, secret: 'PRIVATE-ENERGY' },
      time_of_use: { raw: { ac_output_energy_raw: 300 } } } };
  await change({ native_energy: { 'Office · C1000 Gen 2': nativeEnergy } });
  await refresh();
  const energyPanel = page.getByTestId('native-energy');
  assert.match(await energyPanel.textContent(), /Recent report/);
  assert.match(await energyPanel.textContent(), /1\.250000/);
  assert.match(await energyPanel.textContent(), /Epoch 2/);
  assert.doesNotMatch(await energyPanel.textContent(), /PRIVATE-ENERGY|999/);
  assert.equal(await energyPanel.locator('tbody tr').count(), 4);
  await energyPanel.screenshot({ path: `${root}/docs/images/web-native-energy.png` });
  await change({ native_energy: { 'Office · C1000 Gen 2': { ...nativeEnergy, reported_at: energyNow - 1801,
    counter_epoch_started_at: energyNow - 2000 } } });
  await refresh();
  assert.match(await energyPanel.textContent(), /Stale report/);
  assert.match(await energyPanel.textContent(), /1\.250000/);
  assert.equal(posts, savedPlanPosts);
  await change({ native_energy: {} });
  await refresh();
  assert.match(await energyPanel.textContent(), /Waiting for an energy upload/);
  cases++; console.log(`Scenario ${cases} passed`);

  const planNow = await page.evaluate(() => Date.now() / 1000);
  const freshPlan = { schema_version: 1, enabled: false, source: 'status_d9', reported_at: planNow - 2,
    periods: [{ tariff: 'off_peak', start_hour: 0, end_hour: 6 }, { tariff: 'peak', start_hour: 6, end_hour: 24 }] };
  await change({ plan_readback: freshPlan });
  await refresh();
  assert.match(await page.getByTestId('saved-plan-status').textContent(), /Fresh saved plan/);
  assert.equal(await page.getByTestId('saved-plan-periods').locator('li').count(), 2);
  await page.getByTestId('load-saved-plan').click();
  assert.equal(await page.locator('.period-row').count(), 2);
  assert.equal(await page.locator('.period-row').nth(1).locator('select').inputValue(), 'peak');
  assert.equal(await page.locator('.period-row').nth(1).locator('input').nth(0).inputValue(), '6');
  await page.locator('.period-row').nth(1).locator('input').nth(0).fill('8');
  await refresh();
  assert.equal(await page.locator('.period-row').nth(1).locator('input').nth(0).inputValue(), '8');
  await change({ readonly: true });
  await refresh();
  assert.equal(await page.getByTestId('load-saved-plan').isEnabled(), true);
  await page.getByTestId('load-saved-plan').click();
  assert.equal(await page.locator('.period-row').nth(1).locator('input').nth(0).inputValue(), '6');
  assert.equal(await page.getByRole('button', { name: 'Activate plan', exact: true }).isDisabled(), true);
  await page.screenshot({ path: `${root}/docs/images/web-saved-plan-readback.png`, fullPage: true });
  assert.equal(posts, savedPlanPosts);
  cases++; console.log(`Scenario ${cases} passed`);

  const downloadPending = page.waitForEvent('download');
  await page.getByTestId('settings-export').click();
  const download = await downloadPending;
  const partialSettings = JSON.parse(await readFile(await download.path(), 'utf8'));
  assert.equal(partialSettings.complete, false);
  assert.equal(partialSettings.restore_supported, false);
  assert.equal(partialSettings.field_freshness_verified, false);
  assert.equal(partialSettings.tou_plan_fresh, true);
  assert.equal('name' in partialSettings, false);
  assert.equal('ac_output_enabled' in partialSettings.settings, false);
  assert.equal(posts, savedPlanPosts);
  cases++; console.log(`Scenario ${cases} passed`);

  await change({ readonly: false, plan_readback: { ...freshPlan, reported_at: planNow - 31 } });
  await refresh();
  assert.equal(await page.getByTestId('load-saved-plan').isDisabled(), true);
  assert.match(await page.getByTestId('saved-plan-status').textContent(), /unavailable or stale/);
  await change({ plan_readback: { ...freshPlan, periods: [freshPlan.periods[0], freshPlan.periods[0]] } });
  await refresh();
  assert.equal(await page.getByTestId('load-saved-plan').isDisabled(), true);
  await change({ plan_readback: null });
  await refresh();
  await page.getByRole('button', { name: 'Remove period 2', exact: true }).click();
  await page.getByRole('button', { name: 'Remove period 1', exact: true }).click();
  assert.equal(posts, savedPlanPosts);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#charging-power').selectOption('100');
  await page.getByRole('button', { name: 'Add period' }).click();
  await page.locator('.period-row').first().locator('input').nth(1).fill('6');
  await refresh();
  assert.equal(await page.locator('#charging-power').inputValue(), '100');
  assert.equal(await page.locator('.period-row').first().locator('input').nth(1).inputValue(), '6');
  const beforeChecks = posts;
  const checkRequests = [];
  page.on('request', (request) => {
    if (['/diagnostics', '/setup-check'].some((path) => request.url() === base + path)) {
      checkRequests.push({ method: request.method(), authorization: request.headers().authorization });
    }
  });
  await page.getByTestId('open-checks').click();
  await page.getByTestId('setup-result').filter({ hasText: 'Local file checks passed' }).waitFor();
  assert.equal(await page.locator('.checks-stations li').count(), 5);
  assert.match(await page.getByTestId('gateway-checks').textContent(), /Generated native identity support remains unverified/);
  assert.deepEqual(checkRequests, [{ method: 'GET', authorization }, { method: 'GET', authorization }]);
  await page.screenshot({ path: `${root}/docs/images/web-dashboard-setup-check.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await page.getByTestId('close-checks').isVisible(), true);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByTestId('close-checks').click();
  assert.equal(await page.locator('#charging-power').inputValue(), '100');
  assert.equal(await page.locator('.period-row').first().locator('input').nth(1).inputValue(), '6');
  assert.equal(posts, beforeChecks);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.route('**/setup-check', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  await page.getByTestId('open-checks').click();
  await page.getByTestId('setup-unavailable').waitFor();
  assert.equal(await page.getByTestId('gateway-checks').getByText('Checking…').count(), 0);
  assert.match(await page.getByTestId('gateway-notice').textContent(), /Downloaded cached preferences/);
  await page.getByTestId('close-checks').click();
  await page.unroute('**/setup-check');
  cases++; console.log(`Scenario ${cases} passed`);
  await page.route('**/diagnostics', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }));
  await page.getByTestId('open-checks').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Access denied' }).waitFor();
  assert.equal(await page.getByTestId('gateway-checks').count(), 0);
  assert.equal(posts, beforeChecks);
  await page.unroute('**/diagnostics');
  await connect();
  cases++; console.log(`Scenario ${cases} passed`);

  const beforePreview = posts;
  await page.getByTestId('preview-policy').selectOption('fixed');
  await page.getByTestId('preview-export').fill('700');
  await page.getByTestId('preview-export-sign').check();
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Blocked' }).waitFor();
  assert.match(await page.getByTestId('charging-preview-result').textContent(), /Standard mode is required/);
  await change({ standard: true });
  await refresh();
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Proposed decision' }).waitFor();
  assert.match(await page.getByTestId('charging-preview-result').textContent(), /Reserve → 20%/);
  assert.match(await page.getByTestId('charging-preview-result').textContent(), /Charging power → 1000 W/);
  assert.equal(posts, beforePreview);
  assert.equal(await page.getByTestId('command-review').count(), 0);
  assert.match(await page.getByTestId('gateway-notice').textContent(), /Connected to the local gateway/);
  await page.getByTestId('charging-preview-panel').screenshot({ path: `${root}/docs/images/web-charging-preview.png` });
  await change({ preview_power_w: 300, preview_slot_count: 0 });
  await refresh();
  await page.getByTestId('preview-policy').selectOption('surplus');
  await page.getByTestId('preview-export').fill('900');
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Proposed decision' }).waitFor();
  assert.match(await page.getByTestId('charging-preview-result').textContent(), /Charging power → 500 W/);
  assert.equal(posts, beforePreview);
  assert.equal(await page.getByTestId('command-review').count(), 0);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('preview-policy').selectOption('price_tou');
  await page.getByTestId('preview-price').fill('0.50');
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'peak / battery use' }).waitFor();
  await page.getByTestId('preview-override').selectOption('hold');
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Manual hold blocks proposals' }).waitFor();
  assert.equal(posts, beforePreview);
  await page.getByTestId('preview-policy').selectOption('fixed');
  await change({ preview_power_w: null, preview_slot_count: null });
  await change({ standard: false });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  // Exact controller rules reject the reserve-raising exploratory proposal.
  await change({ standard: true, preview_power_w: 300, preview_slot_count: 0, preview_reserve: 10,
    plan_readback: { schema_version: 1, source: 'status_d9', reported_at: Date.now() / 1000,
      enabled: false, periods: [] } });
  await refresh();
  await page.getByTestId('preview-policy').selectOption('owned_surplus');
  await page.getByTestId('preview-export').fill('900');
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Configure reserve' }).waitFor();
  assert.equal(posts, beforePreview);
  await change({ preview_reserve: 20 });
  await refresh();
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Charging power → 500 W' }).waitFor();
  assert.match(await page.getByTestId('controller-preview-scope').textContent(), /Ownership here is a simulation/);
  assert.equal(await page.getByTestId('command-review').count(), 0);
  await page.getByTestId('charging-preview-panel').screenshot({ path: `${root}/docs/images/web-controller-preview.png` });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('preview-policy').selectOption('owned_price');
  await page.getByTestId('preview-price').fill('0.50');
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'peak / battery use' }).waitFor();
  const current = await fetch(`${base}/devices/${encodeURIComponent('Office · C1000 Gen 2')}`, { headers: { Authorization: authorization } }).then((response) => response.json());
  const protectedKeys = ['ac_charging_power_limit_w', 'max_charge_percentage', 'min_charge_percentage', 'backup_reserve_percentage',
    'ac_fast_charge_enabled', 'ac_output_enabled', 'dc_output_enabled', 'ac_input_connected', 'clock_screen_enabled',
    'clock_screen_transfer_status_raw', 'disaster_preparation_active', 'ac_output_timeout_seconds', 'dc_output_timeout_seconds', 'software_version'];
  await page.getByTestId('preview-ownership').fill(JSON.stringify({ policy: 'surplus', phase: 'active', decision: 'idle',
    changed_at: Date.now() / 1000 - 600, baseline_power_w: 300,
    protected: Object.fromEntries(protectedKeys.map((key) => [key, current.metrics[key]])) }));
  await page.getByTestId('run-charging-preview').click();
  await page.getByTestId('charging-preview-result').filter({ hasText: 'Release the other policy' }).waitFor();
  assert.equal(posts, beforePreview);
  await page.getByTestId('preview-ownership').fill('');
  await page.getByTestId('preview-policy').selectOption('fixed');
  await change({ standard: false, preview_power_w: null, preview_slot_count: null, preview_reserve: null, plan_readback: null });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  // Downsampled synthetic points summarize continuous underlying samples;
  // explicit gaps must break the chart even when selected points have values.
  const savedUntil = Date.now() / 1000;
  const savedPoints = Array.from({ length: 25 }, (_, index) => ({ timestamp: savedUntil - (24 - index) * 3600,
    battery_percentage: 95 - index / 4, ac_input_power_w: 450 + 100 * Math.sin(index / 3),
    ac_output_power_w: 350 + 50 * Math.cos(index / 4), gap: index === 0 || index === 12, max_source_interval_seconds: 5 }));
  await page.route('**/devices/*/history?*', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
    name: 'Office · C1000 Gen 2', model: 'c1000_gen2', protocol: 'native_mqtt', estimated: true,
    points: savedPoints, window: { since: savedUntil - 86400, until: savedUntil },
    totals: { ac_input_energy_kwh_estimate: 10.1, ac_output_energy_kwh_estimate: 8.2,
      ac_input_coverage_seconds: 86000, ac_output_coverage_seconds: 86000, gap_count: 1 },
  }) }));
  await page.waitForFunction(() => !document.querySelector('[data-testid="history-range"] option[value="day"]')?.disabled);
  await page.getByTestId('history-range').selectOption('day');
  await page.getByTestId('history-energy').waitFor();
  assert.match(await page.getByTestId('history-energy').textContent(), /10\.100 kWh/);
  const savedPath = await page.locator('.input-line').getAttribute('d');
  assert.equal((savedPath.match(/M/g) || []).length, 2);
  assert.ok(savedPath.includes('L'));
  assert.match(await page.locator('.history-chart').first().getAttribute('aria-label'), /24 hours/);
  await page.getByTestId('station-history').screenshot({ path: `${root}/docs/images/web-saved-history.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByTestId('history-range').selectOption('session');
  await page.unroute('**/devices/*/history?*');
  assert.equal(posts, beforePreview);
  cases++; console.log(`Scenario ${cases} passed`);

  await propose('charging-power');
  await page.getByTestId('cancel-command').click();
  assert.equal(posts, 0);
  await propose('charging-power');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  let calls = await recorded();
  assert.deepEqual(calls[0], { name: 'Office · C1000 Gen 2', command: 'set-charge-power', watts: 100 });
  assert.equal(posts, 1);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#temperature-unit').selectOption('1');
  await propose('temperature-unit');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  calls = await recorded();
  assert.deepEqual(calls[1], { name: 'Office · C1000 Gen 2', command: 'set-temperature-unit', fahrenheit: true });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#discharge-floor').selectOption('5');
  await propose('discharge-floor');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  calls = await recorded();
  assert.deepEqual(calls[2], { name: 'Office · C1000 Gen 2', command: 'set-discharge-floor', lower: 5 });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#device-timeout').selectOption('30');
  await refresh();
  assert.equal(await page.locator('#device-timeout').inputValue(), '30');
  await propose('device-timeout');
  assert.ok((await page.getByTestId('command-review').textContent()).includes('interrupting remote access'));
  await page.getByTestId('cancel-command').click();
  assert.equal((await recorded()).length, 3);
  await page.locator('#device-timeout').selectOption('0');
  await propose('device-timeout');
  assert.ok((await page.getByTestId('command-review').textContent()).includes('other sleep behavior'));
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[3], { name: 'Office · C1000 Gen 2', command: 'set-device-timeout', minutes: 0 });
  await page.getByTestId('station-select').selectOption('Spare · C1000');
  assert.ok((await page.getByTestId('input-reading').textContent()).includes('AC input not reported'));
  assert.ok((await page.getByTestId('battery-reading').textContent()).includes('Activity unknown'));
  assert.equal(await page.getByTestId('supply-reading').locator('.source-value').textContent(), 'Unknown');
  assert.ok((await page.getByTestId('supply-reading').textContent()).includes('Mode unknown'));
  assert.equal(await page.locator('#device-timeout').inputValue(), '0');
  await page.locator('#device-timeout').selectOption('120');
  await propose('device-timeout');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[4], { name: 'Spare · C1000', command: 'set-device-timeout', minutes: 120 });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.locator('#temperature-unit').selectOption('1');
  await propose('temperature-unit');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[5], { name: 'Spare · C1000', command: 'set-temperature-unit', fahrenheit: true });
  await page.locator('#ac-power-saving').selectOption('1');
  await propose('ac-power-saving');
  assert.ok((await page.getByTestId('command-review').textContent()).includes('automatically turn the output off at low load'));
  await page.getByTestId('cancel-command').click();
  assert.equal((await recorded()).length, 6);
  await propose('ac-power-saving');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[6], { name: 'Spare · C1000', command: 'set-ac-power-saving', enabled: true });
  await page.locator('#dc-power-saving').selectOption('1');
  await propose('dc-power-saving');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[7], { name: 'Spare · C1000', command: 'set-dc-power-saving', enabled: true });
  await page.locator('#fast-charge').selectOption('1');
  await propose('fast-charge');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[8], { name: 'Spare · C1000', command: 'set-fast-charge', enabled: true });
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  assert.equal(await page.locator('#ac-power-saving').count(), 1);
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  assert.equal(await page.locator('#dc-power-saving').count(), 1);
  await page.locator('#fast-charge').selectOption('1');
  const fastApply = page.locator('.setting').filter({ has: page.locator('label[for="fast-charge"]') }).getByRole('button', { name: 'Apply', exact: true });
  assert.equal(await fastApply.isDisabled(), true);
  await change({ standard: true });
  await refresh();
  assert.equal(await fastApply.isDisabled(), false);
  await propose('fast-charge');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[9], { name: 'Office · C1000 Gen 2', command: 'set-fast-charge', enabled: true });
  await change({ standard: false });
  await refresh();
  await page.locator('#fast-charge').selectOption('0');
  await propose('fast-charge');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded())[10], { name: 'Office · C1000 Gen 2', command: 'set-fast-charge', enabled: false });
  await change({ mains: false });
  await refresh();
  assert.equal(await fastApply.isDisabled(), true);
  await change({ mains: true });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Server · C2000 Gen 2');
  assert.equal(await page.locator('#charging-power option[value="100"]').count(), 0);
  assert.equal(await page.locator('#charging-power option[value="200"]').count(), 0);
  assert.equal(await page.locator('#temperature-unit').count(), 0);
  assert.equal(await page.locator('#discharge-floor').count(), 0);
  assert.equal(await page.locator('#off-grid-alert').count(), 0);
  assert.equal(await page.locator('#device-timeout').count(), 0);
  assert.equal(await page.locator('#fast-charge').count(), 0);
  assert.equal(await page.locator('#ac-power-saving').count(), 0);
  assert.equal(await page.locator('#dc-power-saving').count(), 0);
  assert.equal(await page.locator('#display-brightness').count(), 0);
  assert.equal(await page.locator('#display-timeout').count(), 1);
  assert.equal(await page.locator('#port-memory').count(), 0);
  await change({ readonly: true });
  await refresh();
  await page.getByText('This gateway is read-only. Monitoring remains available.').waitFor();
  assert.equal(await page.locator('#charging-power').count(), 0);
  await change({ readonly: false });
  await refresh();
  await change({ available: false });
  await refresh();
  await page.getByText('Stale / unavailable', { exact: true }).waitFor();
  assert.equal(await page.locator('#charging-power').isDisabled(), true);
  assert.match(await fleet.textContent(), /0\/5 with fresh telemetry/);
  assert.equal(await fleet.locator('.fleet-card dd').filter({ hasText: /^Unknown$/ }).count(), 20);
  assert.equal(await fleet.getByText('Telemetry unavailable · power state unknown', { exact: true }).count(), 5);
  await change({ available: true });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.route('**/devices/*/commands', async (route) => {
    await route.fulfill({ status: 504, contentType: 'application/json', body: JSON.stringify({ error: 'PRIVATE-ERROR-TEXT', settings_may_have_changed: true }) });
  }, { times: 1 });
  await propose('charging-power');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Confirmation timed out' }).waitFor();
  await refresh();
  assert.equal(posts, 12);
  assert.equal((await recorded()).length, 11);
  assert.equal((await page.textContent('body')).includes('PRIVATE-ERROR-TEXT'), false);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('check-command-result').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'This request has no available record' }).waitFor();
  assert.equal(posts, 12);
  assert.equal((await recorded()).length, 11);
  cases++; console.log(`Scenario ${cases} passed`);

  let release;
  const barrier = new Promise((resolve) => { release = resolve; });
  let intercept;
  const intercepted = new Promise((resolve) => { intercept = resolve; });
  await page.route('**/devices', async (route) => {
    intercept();
    await barrier;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ devices: [] }) }).catch(() => {});
  }, { times: 1 });
  await page.getByTestId('refresh').click();
  await intercepted;
  await page.getByTestId('disconnect').click();
  release();
  await page.getByTestId('connect').waitFor();
  assert.equal(await page.getByTestId('station-select').count(), 0);
  await connect();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  assert.equal(await page.locator('#display-brightness').inputValue(), '1');
  assert.equal(await page.locator('#display-timeout option[value="0"]').textContent(), 'Never');
  assert.equal(await page.locator('#port-memory').inputValue(), '1');
  const beforePreferences = (await recorded()).length;
  for (const [label, value, command, fields] of [
    ['display-brightness', '2', 'set-display-brightness', { level: 2 }],
    ['display-timeout', '60', 'set-display-timeout', { seconds: 60 }],
    ['port-memory', '0', 'set-port-memory', { enabled: false }],
  ]) {
    await page.locator(`#${label}`).selectOption(value);
    await refresh();
    assert.equal(await page.locator(`#${label}`).inputValue(), value);
    await propose(label);
    if (label === 'port-memory') {
      assert.ok((await page.getByTestId('command-review').textContent()).includes('does not restore that transient state'));
      await page.getByTestId('cancel-command').click();
      assert.equal((await recorded()).length, beforePreferences + 2);
      await propose(label);
    }
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Office · C1000 Gen 2', command, ...fields });
  }
  await change({ available: false });
  await refresh();
  for (const label of ['display-brightness', 'display-timeout', 'port-memory']) {
    assert.equal(await page.locator(`#${label}`).isDisabled(), true);
  }
  await change({ available: true });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Updated · C1000');
  assert.equal(await page.locator('#charging-power').inputValue(), '1000');
  assert.equal(await page.locator('#display-brightness').inputValue(), '2');
  assert.equal(await page.locator('#device-timeout').inputValue(), '720');
  assert.equal(await page.locator('#display-timeout').inputValue(), '30');
  assert.equal(await page.locator('#display-timeout option[value="0"]').count(), 0);
  assert.equal(await page.locator('#display-timeout option[value="10"]').count(), 0);
  assert.equal(await page.locator('#light-mode').inputValue(), '0');
  assert.equal(await page.locator('#temperature-unit').inputValue(), '0');
  for (const label of ['port-memory']) {
    assert.equal(await page.locator(`#${label}`).count(), 0);
  }
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  const beforeOriginalPrime = (await recorded()).length;
  for (const [label, value, command, fields] of [
    ['charging-power', '900', 'set-charge-power', { watts: 900 }],
    ['display-brightness', '1', 'set-display-brightness', { level: 1 }],
    ['device-timeout', '0', 'set-device-timeout', { minutes: 0 }],
    ['display-timeout', '60', 'set-display-timeout', { seconds: 60 }],
    ['light-mode', '1', 'set-light', { mode: 1 }],
    ['temperature-unit', '1', 'set-temperature-unit', { fahrenheit: true }],
    ['dc-power-saving', '1', 'set-dc-power-saving', { enabled: true }],
    ['fast-charge', '1', 'set-fast-charge', { enabled: true }],
  ]) {
    await page.locator(`#${label}`).selectOption(value);
    await propose(label);
    if (label === 'dc-power-saving') {
      assert.match(await page.getByTestId('command-review').textContent(), /DC output OFF.*inactivity counter/s);
    }
    if (label === 'fast-charge') {
      assert.match(await page.getByTestId('command-review').textContent(), /adequate AC supply.*reboot persistence/s);
    }
    await page.getByTestId('cancel-command').click();
    assert.equal((await recorded()).length, beforeOriginalPrime + ['charging-power', 'display-brightness', 'device-timeout', 'display-timeout', 'light-mode', 'temperature-unit', 'dc-power-saving', 'fast-charge'].indexOf(label));
    await propose(label);
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Updated · C1000', command, ...fields });
  }
  await change({ available: false });
  await refresh();
  for (const label of ['charging-power', 'display-brightness', 'device-timeout', 'display-timeout', 'light-mode', 'temperature-unit', 'dc-power-saving', 'fast-charge']) {
    assert.equal(await page.locator(`#${label}`).isDisabled(), true);
  }
  await change({ available: true });
  await refresh();
  const afterOriginalPrime = (await recorded()).length;
  await change({ dc_output: 1 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), true);
  assert.equal(await page.locator('.setting').filter({ has: page.locator('#dc-power-saving') }).getByRole('button', { name: 'Apply', exact: true }).isDisabled(), true);
  assert.equal((await recorded()).length, afterOriginalPrime);
  await change({ dc_output: 0 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), false);
  cases++; console.log(`Scenario ${cases} passed`);

  await change({ ac_output: 0, ac_countdown: 0 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), false);
  const beforeAcSmart = (await recorded()).length;
  for (const value of ['1', '0']) {
    await page.locator('#ac-power-saving').selectOption(value);
    await propose('ac-power-saving');
    assert.match(await page.getByTestId('command-review').textContent(), /AC output OFF.*inactive AC countdown.*inactivity counter/s);
    await page.getByTestId('cancel-command').click();
    assert.equal((await recorded()).length, beforeAcSmart + (value === '1' ? 0 : 1));
    await propose('ac-power-saving');
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Updated · C1000', command: 'set-ac-power-saving', enabled: value === '1' });
  }
  await change({ ac_countdown: 60 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  await change({ ac_countdown: null });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  assert.equal((await recorded()).length, beforeAcSmart + 2);
  assert.equal(await page.locator('#ac-output').count(), 0);
  await change({ ac_output: 1, ac_countdown: 0 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Local · C1000');
  assert.equal(await page.getByTestId('pv-weak-light-lock').count(), 0);
  assert.equal(await page.locator('#charging-power option').first().getAttribute('value'), '100');
  assert.equal(await page.locator('#charging-power option').last().getAttribute('value'), '1000');
  assert.deepEqual(await page.locator('#display-timeout option').evaluateAll((options) => options.map((option) => option.value)), ['20', '30', '60', '300', '1800']);
  for (const label of ['charge-cap', 'backup-reserve', 'discharge-floor', 'port-memory', 'off-grid-alert']) {
    assert.equal(await page.locator(`#${label}`).count(), 0);
  }
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  const beforeNativeOriginal = (await recorded()).length;
  for (const [index, [label, value, command, fields]] of [
    ['charging-power', '900', 'set-charge-power', { watts: 900 }],
    ['display-brightness', '1', 'set-display-brightness', { level: 1 }],
    ['device-timeout', '0', 'set-device-timeout', { minutes: 0 }],
    ['display-timeout', '60', 'set-display-timeout', { seconds: 60 }],
    ['light-mode', '1', 'set-light', { mode: 1 }],
    ['temperature-unit', '1', 'set-temperature-unit', { fahrenheit: true }],
    ['dc-power-saving', '0', 'set-dc-power-saving', { enabled: false }],
    ['fast-charge', '1', 'set-fast-charge', { enabled: true }],
  ].entries()) {
    await page.locator(`#${label}`).selectOption(value);
    await propose(label);
    if (label === 'dc-power-saving') assert.match(await page.getByTestId('command-review').textContent(), /DC output OFF.*inactivity counter/s);
    if (label === 'fast-charge') assert.match(await page.getByTestId('command-review').textContent(), /adequate AC supply.*reboot persistence/s);
    await page.getByTestId('cancel-command').click();
    assert.equal((await recorded()).length, beforeNativeOriginal + index);
    await propose(label);
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Local · C1000', command, ...fields });
  }
  await change({ readonly: true });
  await refresh();
  await page.getByText('This gateway is read-only. Monitoring remains available.').waitFor();
  assert.equal(await page.locator('#charging-power').count(), 0);
  assert.equal(await page.locator('#device-timeout').count(), 0);
  assert.equal(await page.locator('#light-mode').count(), 0);
  assert.ok((await page.getByTestId('input-reading').textContent()).includes('AC input not reported'));
  assert.equal(await page.getByTestId('supply-reading').locator('.source-value').textContent(), 'Unknown');
  await change({ readonly: false, available: false });
  await refresh();
  for (const label of ['charging-power', 'display-brightness', 'device-timeout', 'display-timeout', 'light-mode', 'temperature-unit', 'dc-power-saving', 'fast-charge']) {
    assert.equal(await page.locator(`#${label}`).isDisabled(), true);
  }
  await change({ available: true });
  await refresh();
  const afterNativeOriginal = (await recorded()).length;
  await change({ dc_output: 1 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), true);
  assert.equal((await recorded()).length, afterNativeOriginal);
  await change({ dc_output: 0 });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  await change({ ac_output: 0, ac_countdown: 0 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), false);
  const beforeNativeAcSmart = (await recorded()).length;
  for (const value of ['1', '0']) {
    await page.locator('#ac-power-saving').selectOption(value);
    await propose('ac-power-saving');
    assert.match(await page.getByTestId('command-review').textContent(), /AC output OFF.*inactive AC countdown.*inactivity counter/s);
    await page.getByTestId('cancel-command').click();
    assert.equal((await recorded()).length, beforeNativeAcSmart + (value === '1' ? 0 : 1));
    await propose('ac-power-saving');
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Local · C1000', command: 'set-ac-power-saving', enabled: value === '1' });
  }
  await change({ ac_countdown: 30 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  await change({ ac_countdown: null });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  assert.equal((await recorded()).length, beforeNativeAcSmart + 2);
  assert.equal(await page.locator('#ac-output').count(), 0);
  await change({ ac_output: 1, ac_countdown: 0 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  cases++; console.log(`Scenario ${cases} passed`);

  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  const beforeGen2DcSmart = (await recorded()).length;
  for (const value of ['1', '0']) {
    await page.locator('#dc-power-saving').selectOption(value);
    await propose('dc-power-saving');
    assert.match(await page.getByTestId('command-review').textContent(), /inactive AC\/DC countdowns/);
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  }
  assert.equal((await recorded()).length, beforeGen2DcSmart + 2);
  await change({ dc_output: 1 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), true);
  await change({ dc_output: 0 });
  await refresh();
  assert.equal(await page.locator('#dc-power-saving').isDisabled(), false);
  assert.equal(await page.locator('#ac-power-saving').count(), 1);
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  cases++; console.log(`Scenario ${cases} passed`);

  await change({ standard: true });
  await refresh();
  const beforeGen2Clock = (await recorded()).length;
  for (const window of [1, 2]) {
    await page.locator(`#clock-brightness-${window}`).selectOption('1');
    await propose(`clock-brightness-${window}`);
    assert.match(await page.getByTestId('command-review').textContent(), /Does not enable the clock/);
    await page.getByTestId('confirm-command').click();
    await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
    assert.deepEqual((await recorded()).at(-1), { name: 'Office · C1000 Gen 2', command: 'set-clock-brightness', window, high: true });
  }
  assert.equal((await recorded()).length, beforeGen2Clock + 2);
  await change({ ac_output: 0 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), false);
  await page.locator('#ac-power-saving').selectOption('1');
  await propose('ac-power-saving');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  assert.deepEqual((await recorded()).at(-1), { name: 'Office · C1000 Gen 2', command: 'set-ac-power-saving', enabled: true });
  await page.locator('#ac-power-saving').selectOption('0');
  await propose('ac-power-saving');
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Command confirmed' }).waitFor();
  await change({ ac_output: 1 });
  await refresh();
  assert.equal(await page.locator('#ac-power-saving').isDisabled(), true);
  cases++; console.log(`Scenario ${cases} passed`);

  // Activity and partial comparisons use cached routes, never a command POST.
  const beforeActivity = (await recorded()).length;
  const activityPanel = page.getByTestId('station-activity');
  await activityPanel.locator('summary').click();
  await activityPanel.getByText('Private persisted history', { exact: false }).waitFor();
  await activityPanel.getByTestId('settings-baseline').click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="settings-compare"]')?.disabled);
  const activitySnapshot = await fetch(`${base}/devices`, { headers: { Authorization: authorization } }).then((response) => response.json());
  const changedWatts = activitySnapshot.devices.find((station) => station.name === 'Office · C1000 Gen 2').metrics.ac_charging_power_limit_w === 400 ? 300 : 400;
  await change({ comparison_power_w: changedWatts });
  await activityPanel.getByTestId('settings-compare').click();
  await activityPanel.getByTestId('settings-differences').filter({ hasText: 'ac_charging_power_limit_w' }).waitFor();
  assert.match(await activityPanel.getByTestId('settings-differences').textContent(), new RegExp(String(changedWatts)));
  assert.equal((await recorded()).length, beforeActivity);
  await activityPanel.getByRole('combobox').selectOption('commands');
  assert.match(await activityPanel.getByTestId('activity-records').textContent(), /electrical behavior unverified/);
  assert.match(await activityPanel.getByTestId('activity-records').textContent(), /cached values/);
  await mkdir(`${root}/docs/images`, { recursive: true });
  await activityPanel.screenshot({ path: `${root}/docs/images/web-dashboard-activity.png` });
  await page.getByTestId('station-select').selectOption('Server · C2000 Gen 2');
  assert.equal(await page.getByTestId('station-activity').getByTestId('settings-compare').isDisabled(), true);
  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  assert.equal(await page.getByTestId('station-activity').getByTestId('settings-compare').isDisabled(), true);
  assert.equal((await recorded()).length, beforeActivity);
  await change({ comparison_power_w: null });
  cases++; console.log(`Scenario ${cases} passed`);

  // Review captures expected settings before another client changes the cache.
  await refresh();
  const beforeReview = (await recorded()).length;
  const beforeReviewPosts = posts;
  const reviewSnapshot = await fetch(`${base}/devices`, { headers: { Authorization: authorization } }).then((response) => response.json());
  const reviewWatts = reviewSnapshot.devices.find((station) => station.name === 'Office · C1000 Gen 2').metrics.ac_charging_power_limit_w;
  await page.locator('#charging-power').selectOption('500');
  await propose('charging-power');
  await change({ comparison_power_w: reviewWatts === 300 ? 400 : 300 });
  await page.getByTestId('confirm-command').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'Settings changed during review' }).waitFor();
  assert.equal((await recorded()).length, beforeReview);
  assert.equal(posts, beforeReviewPosts + 1);
  await page.getByTestId('check-command-result').click();
  await page.getByTestId('gateway-notice').filter({ hasText: 'no command was resent' }).waitFor();
  assert.equal(posts, beforeReviewPosts + 1);
  assert.equal((await recorded()).length, beforeReview);
  await change({ comparison_power_w: null });
  await refresh();
  cases++; console.log(`Scenario ${cases} passed`);

  // Controlled browser clock and synthetic read-only responses produce a chart
  // without waiting 20 real minutes or contacting a station.
  const initial = await fetch(`${base}/devices`, { headers: { Authorization: authorization } }).then((response) => response.json());
  let tick = 0;
  await page.route('**/devices', async (route) => {
    const devices = initial.devices.map((station, index) => ({ ...station, last_seen_timestamp: simulatedNow / 1000,
      metrics: { ...station.metrics, battery_percentage: index ? 88 : 92 - tick / 180,
        ac_input_power_w: Math.round(340 + 100 * Math.sin(tick / 17 + index)),
        total_input_power_w: Math.round(340 + 100 * Math.sin(tick / 17 + index)),
        ac_output_power_w: Math.round(490 + 130 * Math.sin(tick / 11 + index)),
        total_output_power_w: Math.round(490 + 130 * Math.sin(tick / 11 + index)),
      } }));
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ devices }) });
  });
  for (tick = 0; tick < 210; tick++) {
    simulatedNow += 5000;
    const response = page.waitForResponse((value) => value.url() === base + '/devices');
    await page.clock.runFor(5000);
    await response;
    await page.waitForFunction(() => !document.querySelector('[data-testid="refresh"]')?.disabled);
  }
  await page.getByTestId('station-select').selectOption('Office · C1000 Gen 2');
  await page.getByRole('button', { name: 'Add period' }).click();
  const second = page.locator('.period-row').nth(1);
  await second.locator('select').selectOption('peak');
  await second.locator('input').nth(0).fill('17');
  await second.locator('input').nth(1).fill('23');
  assert.ok((await page.locator('.input-line').getAttribute('d')).includes('L'));
  await page.locator('footer').evaluate((element) => { element.textContent = 'Synthetic demonstration data · No live stations used'; });
  const screenshots = `${root}/docs/images`;
  await mkdir(screenshots, { recursive: true });
  await page.screenshot({ path: `${screenshots}/web-dashboard-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${screenshots}/web-dashboard-mobile.png`, fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByTestId('station-select').selectOption('Spare · C1000');
  await page.screenshot({ path: `${screenshots}/web-dashboard-c1000-preferences.png`, fullPage: true });
  await page.getByTestId('station-select').selectOption('Updated · C1000');
  await page.screenshot({ path: `${screenshots}/web-dashboard-original-prime.png`, fullPage: true });
  await page.getByTestId('station-select').selectOption('Local · C1000');
  await page.screenshot({ path: `${screenshots}/web-dashboard-original-native.png`, fullPage: true });
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  cases++; console.log(`Scenario ${cases} passed`);
  console.log(`${cases} browser scenarios passed; screenshots contain only synthetic data.`);
} finally {
  if (browser) await browser.close();
  if (fixture.exitCode === null && fixture.signalCode === null) {
    fixture.kill('SIGTERM');
    await once(fixture, 'exit').catch(() => {});
  }
}
