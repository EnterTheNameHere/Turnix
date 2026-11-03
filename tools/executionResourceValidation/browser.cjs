// file: tools/executionResourceValidation/browser.cjs ; version: 1
/** Real G-02 smoke; private connection arrives via stdin and is never written or logged. */
const readline = require('node:readline');
const {createRequire} = require('node:module');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = readline.createInterface({input: process.stdin})[Symbol.asyncIterator]();
(async () => {
  const config = JSON.parse((await input.next()).value);
  const playwright = createRequire(path.resolve(config.playwright, 'package.json'))(config.playwright);
  const browser = await playwright.chromium.launch({executablePath: config.browser, headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1400, height: 1000}});
    await page.goto(config.viewer);
    await page.locator('#endpoint').fill(config.connection.endpoint);
    await page.locator('#publication').fill(config.publication.publicationId);
    await page.locator('#credential').fill(config.connection.readCredential);
    await page.locator('#connect').click();
    await page.waitForFunction(() => ['Connected', 'Stale hardware'].includes(document.querySelector('#status').textContent));
    assert.equal(await page.locator('#credential').inputValue(), '');
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.equal((await page.content()).includes(config.connection.readCredential), false);
    await page.screenshot({path: path.join(config.output, 'g02-connected.png'), fullPage: true});
    process.stdout.write('connected\n');
    await input.next();
    await page.waitForFunction(() => document.querySelector('#status').textContent === 'Revoked');
    await page.screenshot({path: path.join(config.output, 'g02-revoked.png'), fullPage: true});
    process.stdout.write('revoked\n');
  } finally {
    await browser.close();
    input.return();
  }
})().catch(error => { process.stderr.write(error.name + '\n'); process.exitCode = 1; });
