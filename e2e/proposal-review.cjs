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
    await page.getByRole("link", { name: "Order intake" }).click();
    await page.getByLabel("Order file").setInputFiles({
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
    await page.getByLabel("Confirmed quantity").fill("3");
    await page.getByLabel("Unit price").fill("1.20");
    const delivery = new Date();
    delivery.setDate(delivery.getDate() + 30);
    await page.getByLabel("Delivery date").fill(delivery.toISOString().slice(0, 10));
    await page.getByLabel("Currency").fill("SGD");
    await page.getByRole("button", { name: "Confirm order and reserve stock" }).click();
    await page.getByText(/Confirmed order #\d+/).waitFor();
    await page.getByRole("link", { name: "Dashboard", exact: true }).click();
    await page.getByText("ready for delivery", { exact: true }).waitFor();
    await page.getByText("No unread notifications.", { exact: true }).waitFor();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
