// file: tests/tools/observationViewer/browser-smoke.cjs ; version: 1
/** Actual Edge/Playwright smoke driver; private descriptors arrive via stdin, never files/argv/logs. */
const {createInterface} = require('node:readline');
const {createRequire} = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

/** Read framed private process input without echoing credentials. */
function inputLines() {
  const waiting = [], buffered = [];
  const lines = createInterface({input: process.stdin});
  lines.on('line', line => { const value = JSON.parse(line); if (waiting.length) waiting.shift()(value); else buffered.push(value); });
  return () => buffered.length ? Promise.resolve(buffered.shift()) : new Promise(resolve => waiting.push(resolve));
}
const nextLine = inputLines();
/** Send fixed test-control actions to the Python fixture owner, not to an HTTP mutation endpoint. */
async function control(action) { process.stdout.write(JSON.stringify({action}) + '\n'); const reply = await nextLine(); assert.equal(reply.ok, true); }
/** Connect through real visible inputs; the application clears the password field after submit. */
async function connect(page, config, credential = config.connection.readCredential) {
  await page.locator('#endpoint').fill(config.connection.endpoint);
  await page.locator('#publication').fill(config.publication.publicationId);
  await page.locator('#credential').fill(credential);
  await page.locator('#connect').click();
}
/** Assert privacy invariants without printing the credential or retaining screenshots of it. */
async function privacy(page, config) {
  assert.equal(await page.locator('#credential').inputValue(), '');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.equal(await page.evaluate(() => document.cookie), '');
  assert.equal(page.url().includes(config.connection.readCredential), false);
  assert.equal((await page.content()).includes(config.connection.readCredential), false);
}

(async () => {
  const config = await nextLine();
  const playwright = createRequire(path.resolve(process.argv[2], 'package.json'))(process.argv[2]);
  const browser = await playwright.chromium.launch({executablePath: process.argv[3], headless: true});
  const errors = [], urls = []; let active = 0, maximum = 0;
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.name));
  page.on('request', request => { if (request.method() === 'GET' && request.url().startsWith(config.connection.endpoint)) { active++; maximum = Math.max(active, maximum); urls.push(request.url()); } });
  /** Count only this viewer's in-flight bridge GETs, including aborted requests. */
  const settled = request => { if (request.method() === 'GET' && request.url().startsWith(config.connection.endpoint)) active--; };
  page.on('requestfinished', settled); page.on('requestfailed', settled);
  fs.mkdirSync(process.argv[4], {recursive: true});
  try {
    await page.goto(config.origin, {waitUntil: 'networkidle'});
    await connect(page, config);
    await page.waitForFunction(() => ['Connected', 'Stale hardware'].includes(document.getElementById('status').textContent));
    assert.ok(await page.locator('#stream-chart .mark').count());
    await page.locator('#cpu-chart .mark').last().focus();
    assert.match(await page.locator('#details').textContent(), /ns/);
    await privacy(page, config);
    await page.screenshot({path: path.join(process.argv[4], 'connected.png'), fullPage: true});
    await page.locator('#range').selectOption('10');
    await page.locator('#pause').click();
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Paused');
    const count = urls.length;
    await page.waitForTimeout(1300);
    assert.equal(urls.length, count);
    await page.screenshot({path: path.join(process.argv[4], 'paused.png'), fullPage: true});
    const other = await context.newPage();
    await other.goto(config.origin); await connect(other, config);
    await other.waitForFunction(() => ['Connected', 'Stale hardware'].includes(document.getElementById('status').textContent));
    await control('verify-claims');
    await page.locator('#pause').click();
    await page.waitForFunction(() => ['Connected', 'Stale hardware'].includes(document.getElementById('status').textContent));
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Stale hardware', {timeout: 10000});
    await page.screenshot({path: path.join(process.argv[4], 'stale.png'), fullPage: true});
    await connect(page, config, 'wrong-credential-for-test-only');
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Unauthorized');
    assert.equal(await page.locator('#stream-chart .mark').count(), 0);
    await privacy(page, config);
    await page.screenshot({path: path.join(process.argv[4], 'unauthorized.png'), fullPage: true});
    await control('verify-claims');
    await control('revoke');
    await connect(page, config);
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Revoked');
    await page.screenshot({path: path.join(process.argv[4], 'revoked.png'), fullPage: true});
    await control('withdraw');
    await connect(page, config);
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Withdrawn');
    await other.locator('#disconnect').click();
    await privacy(other, config);
    await control('close-bridge');
    await connect(page, config);
    await page.waitForFunction(() => document.getElementById('status').textContent === 'Disconnected');
    await page.screenshot({path: path.join(process.argv[4], 'disconnected.png'), fullPage: true});
    assert.equal(maximum, 1); assert.equal(errors.length, 0);
    assert.ok(urls.every(url => !url.includes('?') && !url.includes(config.connection.readCredential)));
    fs.rmSync(path.join(process.argv[4], 'diagnostic.png'), {force: true});
    process.stdout.write(JSON.stringify({result: 'passed', browser: await browser.version(), maximumOutstandingPerViewer: maximum,
      screenshots: ['connected', 'paused', 'stale', 'unauthorized', 'revoked', 'disconnected'], testedStates: ['connected', 'paused', 'stale', 'unauthorized', 'revoked', 'withdrawn', 'disconnected']}) + '\n');
  } catch (error) {
    const status = await page.locator('#status').textContent().catch(() => 'unavailable');
    const notice = await page.locator('#notice').textContent().catch(() => 'unavailable');
    await page.locator('#credential').fill('').catch(() => {});
    await page.screenshot({path: path.join(process.argv[4], 'diagnostic.png'), fullPage: true}).catch(() => {});
    process.stderr.write(JSON.stringify({status, notice, pageErrorTypes: errors}) + '\n');
    throw error;
  } finally { await context.close(); await browser.close(); }
  process.exit(0);
})().catch(error => { process.stderr.write('Browser smoke failed: ' + error.name + '\n'); process.exit(1); });
