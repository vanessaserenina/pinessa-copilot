const { chromium } = require(process.env.PINESSA_PLAYWRIGHT);

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.PINESSA_CHROMIUM, headless: true });
  try {
    const page = await browser.newPage();
    page.on("pageerror", error => console.error(error.message));
    page.on("response", async response => {
      if (response.url().includes("/api/") && response.status() >= 400) {
        console.error(response.status(), new URL(response.url()).pathname);
      }
    });
    await page.goto(process.env.PINESSA_TEST_URL);
    await page.getByLabel("Email", { exact: true }).fill(process.env.PINESSA_TEST_EMAIL);
    await page.getByLabel("Password").fill(process.env.PINESSA_TEST_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    const queuePath = "**/api/order-sources/pending-review?*";
    await page.route(queuePath, route => {
      const after = new URL(route.request().url()).searchParams.get("after_id");
      const ids = after === "100" ? [101] : Array.from({ length: 100 }, (_, index) => index + 1);
      return route.fulfill({ json: ids });
    });
    await page.getByRole("link", { name: "Order intake" }).click();
    await page.getByRole("button", { name: "Next page" }).click();
    await page.getByLabel("Awaiting review").selectOption("101");
    if (await page.getByLabel("Existing source number").inputValue() !== "101") throw new Error("Missing second-page source");
    await page.getByRole("button", { name: "Previous page" }).click();
    await page.getByLabel("Awaiting review").selectOption("1");
    await page.unroute(queuePath);
    const missing = process.env.PINESSA_QUANTITY_SCENARIO === "missing";
    if (missing) {
      await page.getByLabel("Order message").fill("Customer: TEST Acorn Hotel. PO TEST-PILOT-7. Delivery date 2027-08-18. Blue clips, SKU CLIP-B.");
    } else await page.getByLabel("Order file").setInputFiles({
      name: "TEST-clips.csv", mimeType: "text/csv",
      buffer: Buffer.from('"Synthetic customer Acorn, PO TEST-42: 2.50 cases of blue clips, SKU CLIP-B. Deliver on 2027-03-12."\n'),
    });
    await page.getByRole("button", { name: "Extract proposal" }).click();
    const form = page.getByRole("heading", { name: "Review proposal" });
    const alert = page.getByRole("alert").first();
    await form.or(alert).first().waitFor({ timeout: 260000 });
    if (!await form.isVisible()) throw new Error(await alert.innerText());
    const product = page.getByRole("group", { name: "Line 1", exact: true }).getByRole("combobox");
    if (!await product.inputValue()) throw new Error("Expected automatic blue clip selection");
    await page.getByLabel("Customer code").fill("TEST-ACORN");
    await page.getByLabel("PO number", { exact: true }).fill("TEST-BROWSER-CORRECTED");
    const quantity = page.getByLabel("Confirmed quantity");
    if (await quantity.inputValue() !== "") throw new Error("Quantity must start blank");
    if (!missing) await page.getByText("Requested quantity: 2.50 cases", { exact: true }).waitFor();
    await page.getByLabel("Unit price").fill("1.20");
    const delivery = new Date();
    delivery.setDate(delivery.getDate() + 30);
    await page.getByLabel("Delivery date").fill(delivery.toISOString().slice(0, 10));
    await page.getByLabel("Currency").fill("SGD");
    const submit = page.getByRole("button", { name: "Confirm order and reserve stock" });
    await submit.click();
    if (!await quantity.evaluate(input => input.validity.valueMissing && input === document.activeElement)) {
      throw new Error("Sales must enter the whole-unit quantity");
    }
    await quantity.fill("3");
    await page.getByRole("button", { name: "Confirm order and reserve stock" }).click();
    await page.getByText(/Confirmed order #\d+/).waitFor();
    await page.getByRole("link", { name: "Dashboard", exact: true }).click();
    await page.getByText("ready for delivery", { exact: true }).waitFor();
    await page.getByText("No unread notifications.", { exact: true }).waitFor();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
