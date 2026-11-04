#!/usr/bin/env node
// Dependency-free Chrome smoke test for local files or a preview server:
// node scripts/preview_smoke.cjs [http://127.0.0.1:8768]
const {spawn} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const assert = require("node:assert/strict");

const baseUrl = process.argv[2] || pathToFileURL(path.resolve(__dirname, "../index.html")).href;
const output = fs.mkdtempSync(path.join(os.tmpdir(), "spvtp-page-preview-"));
console.log(`Preview artifacts: ${output}`);
const chromeArgs = ["--headless=new", "--disable-gpu", "--disable-dev-shm-usage",
  "--remote-debugging-pipe", "--no-first-run", "--no-default-browser-check",
  `--user-data-dir=${path.join(output, "profile")}`];
if (process.env.CHROME_NO_SANDBOX === "1") chromeArgs.push("--no-sandbox");
const browser = spawn(process.env.CHROME_BIN || "google-chrome", chromeArgs,
  {stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"]});
let nextId = 0, buffer = "", stderr = "";
const pending = new Map(), exceptions = [];
browser.stderr.on("data", data => { stderr = (stderr + data).slice(-4000); });
browser.on("error", error => { console.error(error); process.exitCode = 1; });
browser.on("exit", () => {
  for (const {reject, timer} of pending.values()) {
    clearTimeout(timer);
    reject(new Error(`Chrome exited: ${stderr}`));
  }
  pending.clear();
});
browser.stdio[4].on("data", data => {
  buffer += data.toString();
  let end;
  while ((end = buffer.indexOf("\0")) >= 0) {
    const message = JSON.parse(buffer.slice(0, end));
    buffer = buffer.slice(end + 1);
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params);
    const request = pending.get(message.id);
    if (request) {
      pending.delete(message.id);
      clearTimeout(request.timer);
      if (message.error) request.reject(new Error(JSON.stringify(message.error)));
      else request.resolve(message.result);
    }
  }
});

function send(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP timed out: ${method}`));
    }, 20000);
    pending.set(id, {resolve, reject, timer});
    browser.stdio[3].write(JSON.stringify({id, method, params, sessionId}) + "\0");
  });
}

async function main() {
  const {targetId} = await send("Target.createTarget", {url: "about:blank"});
  const {sessionId} = await send("Target.attachToTarget", {targetId, flatten: true});
  const command = (method, params = {}) => send(method, params, sessionId);
  const evaluate = async expression => {
    const result = await command("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
    assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await command("Page.enable");
  await command("Runtime.enable");
  // External typography/icon CDNs are not needed to test the site's own assets.
  // Keep this smoke test deterministic even on nodes without CDN connectivity.
  await command("Network.enable");
  await command("Network.setBlockedURLs", {urls: ["https://fonts.googleapis.com/*",
    "https://fonts.gstatic.com/*", "https://cdn.jsdelivr.net/*"]});
  await command("Emulation.setDeviceMetricsOverride", {width: 1440, height: 1100,
    deviceScaleFactor: 1, mobile: false});
  await command("Page.navigate", {url: baseUrl});
  for (let tries = 0; tries < 100; tries++) {
    if (await evaluate('document.readyState !== "loading" && !!document.getElementById("rollout-results")')) break;
    assert(tries < 99, "Page did not finish parsing");
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  await evaluate("Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 3000))]).then(() => true)");
  await evaluate("document.documentElement.style.scrollBehavior = 'auto'; true");
  await evaluate("Promise.all(Array.from(document.images).filter(i => i.loading !== 'lazy').map(i => i.complete ? true : new Promise(r => { i.onload = i.onerror = () => r(true); }))).then(() => true)");
  assert.equal(await evaluate('document.querySelectorAll("table.results-table").length'), 7);

  async function capture(width, mobile, anchor, file) {
    await command("Emulation.setDeviceMetricsOverride", {width, height: 1100,
      deviceScaleFactor: 1, mobile});
    await evaluate(`document.getElementById(${JSON.stringify(anchor)}).scrollIntoView(); true`);
    await evaluate("new Promise(resolve => setTimeout(() => resolve(true), 600))");
    const dimensions = await evaluate("({width: innerWidth, scroll: document.documentElement.scrollWidth})");
    assert(dimensions.scroll <= width + 1, `Page overflows at ${width}px: ${JSON.stringify(dimensions)}`);
    const {data} = await command("Page.captureScreenshot", {format: "png"});
    fs.writeFileSync(path.join(output, file), Buffer.from(data, "base64"));
  }
  await capture(1440, false, "results", "desktop-results.png");
  await capture(1440, false, "feedback", "desktop-feedback.png");
  await capture(390, true, "feedback", "mobile-feedback.png");
  await evaluate('document.querySelector(".navbar-burger").click(); true');
  assert.equal(await evaluate('document.querySelector(".navbar-burger").getAttribute("aria-expanded")'), "true");
  await evaluate(`document.querySelector('.navbar-menu a[href="#feedback"]').click(); true`);
  assert.equal(await evaluate('document.querySelector(".navbar-burger").getAttribute("aria-expanded")'), "false");
  await evaluate('document.querySelectorAll("details").forEach(el => { el.open = true; }); true');
  await capture(390, true, "results", "mobile-results-expanded.png");
  const brokenImages = await evaluate('Array.from(document.images).filter(i => i.complete && !i.naturalWidth).map(i => i.src)');
  assert.deepEqual(brokenImages, [], "Broken images");
  assert.deepEqual(exceptions, [], "Browser JavaScript exceptions");
  console.log("PASS: desktop/mobile layout, seven tables, expandable details, mobile menu, and image loading.");
  console.log(`Screenshots: ${output}`);
}

main().catch(error => { console.error(error, stderr); process.exitCode = 1; })
  .finally(() => { browser.kill("SIGTERM"); });
