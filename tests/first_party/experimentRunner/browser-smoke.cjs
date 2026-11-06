// file: tests/first_party/experimentRunner/browser-smoke.cjs ; version: 1
/** Actual unchanged G-02 viewer against Experiment Runner; credentials only in private stdin/module memory. */
const {createInterface} = require('node:readline');
const {createRequire} = require('node:module');
const path = require('node:path');
const assert = require('node:assert/strict');
const lines = createInterface({input: process.stdin});
const buffered = [], waiting = [];
lines.on('line', line => { const value = JSON.parse(line); if (waiting.length) waiting.shift()(value); else buffered.push(value); });
/** Read a single framed notice/control response without printing it. */
const next = () => buffered.length ? Promise.resolve(buffered.shift()) : new Promise(resolve => waiting.push(resolve));
/** Invoke only fixed test-fixture actions, never HTTP mutation endpoints. */
async function control(action) { process.stdout.write(JSON.stringify({action}) + '\n'); assert.equal((await next()).ok, true); }
/** Use the existing viewer's visible, explicit connection form. */
async function connect(page, config) {
  await page.locator('#endpoint').fill(config.connection.endpoint);
  await page.locator('#publication').fill(config.publication.publicationId);
  await page.locator('#credential').fill(config.connection.readCredential);
  await page.locator('#connect').click();
}
/** Check browser credential lifetime/privacy without serializing the credential into any diagnostics. */
async function privacy(page, config) {
  assert.equal(await page.locator('#credential').inputValue(), '');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.equal(await page.evaluate(() => document.cookie), '');
  assert.equal(page.url().includes(config.connection.readCredential), false);
  assert.equal((await page.content()).includes(config.connection.readCredential), false);
}
(async () => {
  const config = await next();
  const playwright = createRequire(path.resolve(process.argv[2], 'package.json'))(process.argv[2]);
  const browser = await playwright.chromium.launch({executablePath: process.argv[3], headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}});
  const page = await context.newPage();
  let count = 0, active = 0, maximum = 0;
  const errors = [];
  page.on('pageerror', () => errors.push('pageerror'));
  page.on('request', request => { if (request.url().startsWith(config.connection.endpoint)) {
    assert.equal(request.url().includes(config.connection.readCredential), false);
    count++; active++; maximum = Math.max(active, maximum);
  }});
  const settle = request => { if (request.url().startsWith(config.connection.endpoint)) active--; };
  page.on('requestfinished', settle); page.on('requestfailed', settle);
  try {
    await page.goto(config.origin);
    await connect(page, config);
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Connected');
    await page.waitForFunction(() => document.querySelectorAll('#stream-chart .mark').length > 0);
    await privacy(page, config);
    await page.screenshot({path: path.join(process.argv[4], 'connected.png'), fullPage: true});
    await page.locator('#range').selectOption('10');
    await page.locator('#pause').click();
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Paused');
    await page.waitForTimeout(300);
    const pausedCount = count;
    const peer = await context.newPage();
    await peer.goto(config.origin); await connect(peer, config);
    await peer.waitForFunction(() => document.getElementById('status').textContent === 'Connected');
    await page.waitForTimeout(1100);
    assert.equal(count, pausedCount);
    await control('verify-active');
    await page.screenshot({path: path.join(process.argv[4], 'paused.png'), fullPage: true});
    await page.locator('#disconnect').click();
    assert.equal(await page.locator('#status').textContent(), 'Disconnected');
    await control('verify-active');
    await control('release-call');
    await peer.waitForTimeout(1100);
    await privacy(peer, config);
    await peer.screenshot({path: path.join(process.argv[4], 'call-final.png'), fullPage: true});
    await control('revoke');
    await peer.waitForFunction(() => document.getElementById('status').textContent === 'Revoked');
    await peer.screenshot({path: path.join(process.argv[4], 'revoked.png'), fullPage: true});
    await control('finish');
    await connect(page, config);
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Disconnected');
    await privacy(page, config); await privacy(peer, config);
    assert.equal(maximum, 1); assert.equal(errors.length, 0);
    process.stdout.write(JSON.stringify({result: 'passed', browser: await browser.version(), maximumOutstandingPerViewer: maximum,
      screenshots: ['connected', 'paused', 'call-final', 'revoked'],
      testedStates: ['connected', 'paused', 'peer-independent', 'disconnected-reader', 'call-final', 'revoked', 'endpoint-closed']}) + '\n');
  } finally { await context.close(); await browser.close(); }
  process.exit(0);
})().catch(error => { process.stderr.write('Browser smoke failed: ' + error.name + '\n'); process.exit(1); });
