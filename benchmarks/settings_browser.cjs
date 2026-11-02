// Use NODE_PATH for an installed Playwright and Axe. This gate installs nothing.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { chromium } = require("playwright");
const { default: AxeBuilder } = require("@axe-core/playwright");

async function main() {
  const output = path.resolve(
    process.argv[2] || "/tmp/blueprint-settings-browser",
  );
  await fs.mkdir(output, { recursive: true });
  const root = await fs.mkdtemp(
    path.join(os.tmpdir(), "blueprint-settings-fixture-"),
  );
  const python = process.env.BLUEPRINT_PYTHON || "python3";
  const env = {
    ...process.env,
    PYTHONPATH: process.env.BLUEPRINT_PACKAGE_ROOT || path.resolve("src"),
  };
  await fs.writeFile(
    path.join(root, "pyproject.toml"),
    '[project]\nname="example"\nversion="1.0.0"\n',
  );
  await fs.writeFile(
    path.join(root, ".blueprint-ai.yml"),
    "# Synthetic fixture\nmodel_mode: off\n",
  );
  await fs.writeFile(path.join(root, "keep.py"), "keep = True\n");
  await fs.mkdir(path.join(root, "scratch"));
  await fs.writeFile(
    path.join(root, "scratch", "generated.py"),
    "generated = True\n",
  );
  const cli = (...args) => {
    const result = spawnSync(python, ["-m", "blueprint_ai.cli", ...args], {
      env,
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  const show = () => cli("settings", "show", root, "--json");
  const setting = (id) => show().settings.find((s) => s.id === id);
  const server = spawn(
    python,
    ["-u", "-m", "blueprint_ai.cli", "settings", "serve", root],
    { env },
  );
  let browser;
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("Local server did not start")),
        15000,
      );
      server.once("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`Server exited ${code}`));
      });
      server.stdout.on("data", (chunk) => {
        const match = chunk.toString().match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) {
          clearTimeout(timer);
          resolve(match[0]);
        }
      });
      server.stderr.on("data", (chunk) => process.stderr.write(chunk));
    });
    browser = await chromium.launch({
      channel: "chrome",
      headless: true,
      chromiumSandbox: true,
    });
    const context = await browser.newContext({
      viewport: { width: 1280, height: 900 },
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url);
    await page
      .getByRole("status")
      .filter({ hasText: "Saved settings loaded" })
      .waitFor();
    assert.equal(await page.locator("article").count(), 7);
    await page.getByLabel("Search settings").fill("worker");
    assert.equal(await page.locator("article").count(), 1);
    await page.getByLabel("Parallel workers").fill("8");
    assert.equal(
      await page
        .getByRole("button", { name: "Save changes", exact: true })
        .isDisabled(),
      true,
    );
    await page.getByLabel("Search settings").fill("");
    await page
      .getByLabel("Maximum priority", { exact: true })
      .selectOption('"P0"');
    const save = async () => {
      await page.getByRole("button", { name: "Preview changes" }).click();
      await page
        .getByRole("status")
        .filter({ hasText: "Preview validated" })
        .waitFor();
      await page
        .getByRole("button", { name: "Save changes", exact: true })
        .click();
      await page
        .getByRole("status")
        .filter({ hasText: "Saved. The next operation" })
        .waitFor();
    };
    await save();
    assert.equal(setting("max_workers").effective, 8);
    assert.equal(cli("inspect", root, "--json").languages.Python, 2);
    await page.getByLabel("Additional ignored paths").fill("scratch/");
    await save();
    assert.deepEqual(setting("ignores").effective, ["scratch/"]);
    assert.equal(cli("inspect", root, "--json").languages.Python, 1);
    await page.reload();
    await page
      .getByRole("status")
      .filter({ hasText: "Saved settings loaded" })
      .waitFor();
    assert.equal(
      await page.getByLabel("Additional ignored paths").inputValue(),
      "scratch/",
    );
    await page
      .locator("article")
      .filter({ has: page.getByLabel("Additional ignored paths") })
      .getByRole("button", { name: "Use default" })
      .click();
    await save();
    assert.equal(cli("inspect", root, "--json").languages.Python, 2);
    await page.getByLabel("Additional ignored paths").fill("x".repeat(1001));
    await page.getByRole("button", { name: "Preview changes" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Invalid settings" })
      .waitFor();
    assert.deepEqual(setting("ignores").effective, []);
    await page.getByRole("button", { name: "Reload saved settings" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Saved settings loaded" })
      .waitFor();
    const review = cli(
      "review",
      root,
      "--blueprint",
      "repository",
      "--no-model",
      "--json",
    );
    assert.equal(review.results.flatMap((r) => r.findings).length, 0);
    await page.reload();
    await page
      .getByRole("status")
      .filter({ hasText: "Saved settings loaded" })
      .waitFor();
    assert.equal(await page.getByLabel("Parallel workers").inputValue(), "8");
    await page.getByLabel("Category", { exact: true }).selectOption("Reports");
    assert.equal(await page.locator("article").count(), 3);
    await page
      .locator("article")
      .filter({ has: page.getByLabel("Maximum priority", { exact: true }) })
      .getByRole("button", { name: "Use default" })
      .click();
    await save();
    assert.equal(setting("maximum_priority").source, "default");
    assert.ok(
      cli(
        "review",
        root,
        "--blueprint",
        "repository",
        "--no-model",
        "--json",
      ).results.flatMap((r) => r.findings).length > 0,
    );
    await page.getByLabel("Category", { exact: true }).selectOption("");
    cli("settings", "set", "max_workers", "12", root, "--json");
    await page.getByLabel("Tool timeout").fill("30");
    await page.getByRole("button", { name: "Preview changes" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Settings changed" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Save changes", exact: true })
        .isDisabled(),
      true,
    );
    await page.getByRole("button", { name: "Reload saved settings" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Saved settings loaded" })
      .waitFor();
    assert.equal(await page.getByLabel("Parallel workers").inputValue(), "12");
    await page.getByLabel("Parallel workers").fill("0");
    await page.getByRole("button", { name: "Preview changes" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Invalid settings" })
      .waitFor();
    assert.equal(setting("max_workers").effective, 12);
    await page.getByLabel("Parallel workers").fill("8");
    await page.getByRole("button", { name: "Preview changes" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Preview validated" })
      .waitFor();
    let writes = 0;
    await page.route("**/api/settings", async (route) => {
      const request = route.request();
      if (
        request.method() === "POST" &&
        request.postDataJSON().action === "save"
      ) {
        writes++;
        await route.fetch();
        await route.abort("failed");
      } else await route.continue();
    });
    await page
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    await page
      .getByRole("status")
      .filter({ hasText: "Save was not confirmed" })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Save changes", exact: true })
        .isDisabled(),
      true,
    );
    assert.equal(setting("max_workers").effective, 8);
    await page.unroute("**/api/settings");
    await page.getByRole("button", { name: "Reload saved settings" }).click();
    await page
      .getByRole("status")
      .filter({ hasText: "Saved settings loaded" })
      .waitFor();
    assert.equal(writes, 1);
    const audit = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    assert.deepEqual(audit.violations, []);
    await page.screenshot({
      path: path.join(output, "settings-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.getByLabel("Search settings").focus();
    assert.equal(
      await page
        .getByLabel("Search settings")
        .evaluate((e) => e === document.activeElement),
      true,
    );
    await page.screenshot({
      path: path.join(output, "settings-mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    await fs.writeFile(
      path.join(output, "results.json"),
      JSON.stringify(
        {
          result: "passed",
          browser: browser.version(),
          synthetic: true,
          modelCalls: 0,
          coverage: [
            "additional ignored paths change native inspection and reset",
            "search",
            "categories",
            "preview",
            "browser-to-cli persistence",
            "real review filters",
            "reset",
            "cold reload",
            "stale edit",
            "invalid value",
            "lost acknowledgement without replay",
            "keyboard focus",
            "WCAG2 A/AA Axe",
            "mobile overflow",
          ],
          pageErrors: errors,
        },
        null,
        2,
      ) + "\n",
    );
    console.log("Blueprint settings browser workflow passed.");
  } finally {
    if (browser) await browser.close();
    server.kill("SIGTERM");
    await fs.rm(root, { recursive: true, force: true });
  }
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
