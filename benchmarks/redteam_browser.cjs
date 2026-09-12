// Independent browser gate. Applications are supplied by bounded local containers.
// Use NODE_PATH to select an already installed Playwright; no dependency is installed here.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const [reactUrl, fullstackUrl, output = '/tmp/blueprint-redteam-browser'] = process.argv.slice(2);
  if (!reactUrl || !fullstackUrl) throw new Error('Supply React URL, full-stack URL, output directory');
  for (const value of [reactUrl, fullstackUrl]) {
    if (new URL(value).hostname !== '127.0.0.1') throw new Error('Only controlled loopback apps allowed');
  }
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true, chromiumSandbox: true });
  const rows = [];
  try {
    for (const [kind, url] of [['react', reactUrl], ['full-stack', fullstackUrl]]) {
      const context = await browser.newContext();
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const started = Date.now();
      const response = await page.goto(url, { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      assert.equal(await page.getByRole('heading', { level: 1 }).textContent(), 'Workspace is ready');
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.getByRole('main').isVisible(), true);
      if (kind === 'full-stack') {
        assert.equal(await page.getByRole('status').textContent(), 'Ready');
        const health = page.waitForResponse('**/api/health');
        await page.getByRole('button', { name: 'Check service' }).click();
        const actualHealth = await health;
        assert.equal(actualHealth.status(), 200);
        assert.deepEqual(await actualHealth.json(), { status: 'ok' });
        await page.getByRole('status').filter({ hasText: 'Service is healthy' }).waitFor();
        // After the real request, separately exercise client-side failure presentation.
        await page.route('**/api/health', route => route.abort());
        await page.getByRole('button', { name: 'Check service' }).focus();
        await page.keyboard.press('Enter');
        await page.getByRole('status').filter({ hasText: 'Service unavailable' }).waitFor();
        await page.unroute('**/api/health');
        await page.getByRole('button', { name: 'Check service' }).click();
        await page.getByRole('status').filter({ hasText: 'Service is healthy' }).waitFor();
      }
      assert.deepEqual(errors, []);
      await page.screenshot({ path: path.join(output, `${kind}.png`), fullPage: true });
      rows.push({ kind, status: 'passed', url, browser: browser.version(), duration_ms: Date.now() - started,
        real_backend: kind === 'full-stack', page_errors: errors });
      await context.close();
    }
  } finally { await browser.close(); }
  await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(rows, null, 2) + '\n');
  console.log(JSON.stringify(rows, null, 2));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
