// Use the existing settings server and installed browser tooling. No project write is sent.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { default: AxeBuilder } = require("@axe-core/playwright");
const KEY = "blueprint-ai.appearance.v1";

async function open(page) {
  const details = page.locator("#appearance-settings");
  if (!(await details.evaluate((e) => e.open)))
    await details.locator("summary").click();
}
async function theme(page, id) {
  await page.waitForFunction(
    (id) => document.documentElement.dataset.theme === id,
    id,
  );
}

async function preserveDraft(page) {
  const draft = await page.locator("#draft").innerText();
  const save = await page.locator("#save").isDisabled();
  const worker = await page.locator("#setting-max_workers").inputValue();
  await open(page);
  await page
    .getByLabel("Theme preset", { exact: true })
    .selectOption("slate-dark");
  await theme(page, "slate-dark");
  assert.equal(await page.locator("#draft").innerText(), draft);
  assert.equal(await page.locator("#save").isDisabled(), save);
  assert.equal(await page.locator("#setting-max_workers").inputValue(), worker);
}

async function verifyAppearance(browser, url, output) {
  const context = await browser.newContext({
    colorScheme: "light",
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  const posts = [];
  const external = [];
  const scans = [];
  page.on("pageerror", (e) => errors.push(e.message));
  context.on("page", (p) => p.on("pageerror", (e) => errors.push(e.message)));
  context.on("request", (r) => {
    if (r.method() === "POST") posts.push(r.url());
    if (!r.url().startsWith(url)) external.push(r.url());
  });
  try {
    await page.goto(url);
    await open(page);
    await theme(page, "neon1-light");
    await page.locator("#setting-max_workers").waitFor();
    await page.emulateMedia({ colorScheme: "dark" });
    await theme(page, "neon1-dark");
    for (const preset of [
      "neon1-dark",
      "neon1-light",
      "slate-dark",
      "sand-light",
      "forest-light",
      "matrix-dark",
    ]) {
      await page
        .getByLabel("Theme preset", { exact: true })
        .selectOption(preset);
      await theme(page, preset);
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: 900 });
        assert.equal(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          true,
          `${preset} at${width}`,
        );
        const colors = await page.evaluate(() => {
          const s = getComputedStyle(document.documentElement);
          return {
            paint: s.backgroundColor,
            token: s.getPropertyValue("--theme-surface-canvas").trim(),
          };
        });
        const rgb = colors.token.match(
          /^#([a-f0-9]{2})([a-f0-9]{2})([a-f0-9]{2})$/i,
        );
        assert.ok(rgb);
        assert.equal(
          colors.paint,
          `rgb(${parseInt(rgb[1], 16)}, ${parseInt(rgb[2], 16)}, ${parseInt(rgb[3], 16)})`,
        );
        const axe = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa"])
          .analyze();
        assert.deepEqual(axe.violations, [], `${preset} at${width}`);
        scans.push({ preset, width, violations: 0 });
        await page.screenshot({
          path: path.join(output, `appearance-${preset}-${width}.png`),
          fullPage: true,
        });
      }
    }
    await page
      .getByLabel("Theme preset", { exact: true })
      .selectOption("forest-light");
    await theme(page, "forest-light");
    await page.reload();
    await open(page);
    await theme(page, "forest-light");
    await page.getByLabel("Density", { exact: true }).selectOption("compact");
    await page
      .getByRole("button", { name: "Apply changes", exact: true })
      .click();
    assert.equal(
      await page.locator("html").getAttribute("data-theme-density"),
      "compact",
    );
    await page.getByLabel("Accent color", { exact: true }).evaluate((e) => {
      e.value = "#ffffff";
      e.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page
      .getByRole("button", { name: "Apply changes", exact: true })
      .click();
    assert.match(
      await page.locator("#appearance-status").innerText(),
      /cannot be applied/,
    );
    assert.equal(
      await page.locator("html").getAttribute("data-theme"),
      "forest-light",
    );
    assert.ok(
      !(await page.evaluate((key) => localStorage.getItem(key), KEY)).includes(
        "#ffffff",
      ),
    );
    await page.evaluate(() =>
      localStorage.setItem("deep.appearance.v1", "independent"),
    );
    const second = await context.newPage();
    await second.goto(url);
    await open(second);
    await second
      .getByLabel("Theme preset", { exact: true })
      .selectOption("matrix-dark");
    await theme(page, "matrix-dark");
    await second
      .getByRole("button", { name: "Reset appearance", exact: true })
      .click();
    await theme(page, "neon1-dark");
    assert.equal(
      await page.evaluate(() => localStorage.getItem("deep.appearance.v1")),
      "independent",
    );
    await second.close();
    const invalid = '{"version":99}';
    await page.evaluate(
      ({ key, invalid }) => localStorage.setItem(key, invalid),
      { key: KEY, invalid },
    );
    await page.reload();
    await open(page);
    await theme(page, "neon1-dark");
    assert.match(
      await page.locator("#appearance-status").innerText(),
      /Saved appearance is invalid/,
    );
    assert.equal(
      await page.evaluate((key) => localStorage.getItem(key), KEY),
      invalid,
    );
    await page
      .getByRole("button", { name: "Reset appearance", exact: true })
      .click();
    await page.locator("#appearance-settings > summary").focus();
    await page.keyboard.press("Space");
    assert.equal(
      await page.locator("#appearance-settings").evaluate((e) => e.open),
      false,
    );
    await page.keyboard.press("Enter");
    assert.equal(
      await page.locator("#appearance-settings").evaluate((e) => e.open),
      true,
    );
    await page.getByLabel("Theme preset", { exact: true }).focus();
    await page.keyboard.press("Tab");
    assert.equal(
      await page
        .getByLabel("Accent color", { exact: true })
        .evaluate((e) => e === document.activeElement),
      true,
    );
    assert.deepEqual(posts, []);
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
  } finally {
    await context.close();
  }
  for (const kind of ["methods", "getter"]) {
    const blocked = await browser.newContext({
      viewport: { width: 320, height: 900 },
    });
    try {
      await blocked.addInitScript((kind) => {
        const fail = () => {
          throw new DOMException("blocked", "SecurityError");
        };
        if (kind === "getter")
          Object.defineProperty(window, "localStorage", { get: fail });
        else
          for (const method of ["getItem", "setItem", "removeItem"])
            Object.defineProperty(Storage.prototype, method, { value: fail });
      }, kind);
      const p = await blocked.newPage();
      await p.goto(url);
      await open(p);
      await p
        .getByLabel("Theme preset", { exact: true })
        .selectOption("sand-light");
      await theme(p, "sand-light");
      assert.match(
        await p.locator("#appearance-status").innerText(),
        /only to this page/,
      );
      await p.getByLabel("Parallel workers").fill("11");
      assert.equal(await p.locator("#preview").isEnabled(), true);
      assert.equal(await p.locator("#save").isDisabled(), true);
      assert.equal(
        await p.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        true,
      );
    } finally {
      await blocked.close();
    }
  }
  return {
    scans,
    projectPosts: posts.length,
    externalRequests: external.length,
    pageErrors: errors,
    checks: [
      "System/OS changes",
      "all six presets paint native settings",
      "reload persistence",
      "compact density",
      "unsafe override refusal",
      "cross-tab reset",
      "independent key",
      "invalid preference preservation",
      "blocked storage methods/getter",
      "keyboard details and tab focus",
      "project edits remain usable",
    ],
  };
}
module.exports = { preserveDraft, verifyAppearance };
