import { useState } from "react";
import type { FormEvent } from "react";
import { decimalText, errorMessage, jsonPost, useQuery } from "./api";
import type { Api, Product, ProductPage } from "./api";

type Line = { product_id: number; quantity: number; unit_price: string };
type Need = { product_id: number; shortfall: string; from_stock: string };
type Summary = { id: number; customer_code: string; status: string };
type Order = Summary & {
  customer_po_number: string | null; requested_delivery_date: string | null; currency: string;
  revision: number; lines: Line[]; subtotal: string;
  stock: { finished_goods: Need[]; components: Need[] } | null;
};

function ProductLabel({ api, id }: { api: Api; id: number }) {
  const product = useQuery<Product>(api, `/products/${id}`);
  return <>{product.data ? `${product.data.sku} · ${product.data.name}` : `Product #${id}`}</>;
}

function OrderForm({ api, order, onSaved }: { api: Api; order?: Order; onSaved: (order: Order) => void }) {
  const [lines, setLines] = useState<Line[]>(order?.lines ?? []);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [dirty, setDirty] = useState(false);
  const products = useQuery<ProductPage>(api, search.trim()
    ? `/products?product_type=FINISHED&q=${encodeURIComponent(search.trim())}&limit=20` : null);

  function changed() {
    setDirty(true);
    setDuplicate(false);
    setAcknowledged(false);
    setError("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!lines.length) { setError("Add at least one product."); return; }
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    const data = { ...fields, customer_po_number: fields.customer_po_number || null,
      requested_delivery_date: fields.requested_delivery_date || null, lines,
      acknowledge_duplicate: acknowledged, ...(order ? { revision: order.revision } : {}) };
    setBusy(true);
    setError("");
    try {
      onSaved(await api<Order>(order ? `/orders/${order.id}` : "/orders", {
        ...jsonPost(data), method: order ? "PUT" : "POST",
      }));
    } catch (err) {
      const message = errorMessage(err);
      setError(message);
      setDuplicate(message.startsWith("Possible duplicate of order"));
    } finally { setBusy(false); }
  }

  async function confirm() {
    if (!order) return;
    setBusy(true);
    setError("");
    try {
      onSaved(await api<Order>(`/orders/${order.id}/confirm`, jsonPost({ revision: order.revision })));
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  return <form className="panel" onSubmit={save}>
    <h2>{order ? `Order #${order.id}` : "New order"}</h2>
    {order && <p role="status">{order.status.replaceAll("_", " ")} · Revision {order.revision} · Subtotal {order.currency} {decimalText(order.subtotal)}</p>}
    <fieldset disabled={busy} onChange={changed}>
      <label>Customer code<input name="customer_code" required maxLength={80} defaultValue={order?.customer_code} /></label>
      <label>Customer PO number<input name="customer_po_number" maxLength={120} defaultValue={order?.customer_po_number ?? ""} /></label>
      <label>Requested delivery date<input name="requested_delivery_date" type="date" defaultValue={order?.requested_delivery_date ?? ""} /></label>
      <label>Currency<input name="currency" required pattern="[A-Za-z]{3}" maxLength={3} placeholder="SGD" defaultValue={order?.currency} /></label>
      <h3>Order lines</h3>
      {lines.map((line, index) => <div className="actions" key={index}>
        <ProductLabel api={api} id={line.product_id} />
        <label>Quantity<input type="number" min={1} max={10000000} required value={line.quantity || ""}
          onChange={e => setLines(lines.map((v, i) => i === index ? { ...v, quantity: Number(e.target.value) } : v))} /></label>
        <label>Unit price<input type="number" min="0" step="0.0001" required value={line.unit_price}
          onChange={e => setLines(lines.map((v, i) => i === index ? { ...v, unit_price: e.target.value } : v))} /></label>
        <button type="button" onClick={() => { changed(); setLines(lines.filter((_, i) => i !== index)); }}>Remove</button>
      </div>)}
      <label>Find a product<input maxLength={100} value={search} onChange={e => setSearch(e.target.value)} placeholder="SKU or product name" /></label>
      {products.error && <p role="alert">{products.error}</p>}
      {products.data && <ul>{products.data.items.filter(p => p.is_active).map(p => <li key={p.id}>
        <button type="button" onClick={() => { changed(); setLines([...lines, { product_id: p.id, quantity: 1, unit_price: "" }]); setSearch(""); }}>
          Add {p.sku} · {p.name}</button>
      </li>)}</ul>}
      {products.data?.items.length === 0 && <p>No matching products.</p>}
      {order && <label>Reason for edit<input name="reason" required maxLength={500} /></label>}
    </fieldset>
    {order?.stock && <section><h3>Stock at confirmation or last edit</h3>
      {[...order.stock.finished_goods, ...order.stock.components].map(n => <p key={n.product_id}>
        <ProductLabel api={api} id={n.product_id} /> · {decimalText(n.from_stock)} reserved · {decimalText(n.shortfall)} short
      </p>)}</section>}
    {error && <p className="error" role="alert">{error}</p>}
    {duplicate && <label><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} /> I reviewed the possible duplicate.</label>}
    <div className="actions">
      <button className="primary" disabled={busy || (duplicate && !acknowledged)}>{order ? "Save edits" : "Create draft"}</button>
      {order?.status === "draft" && <button type="button" disabled={busy || dirty} onClick={confirm}>Confirm and reserve stock</button>}
    </div>
  </form>;
}

export function ManualOrders({ api }: { api: Api }) {
  const [id, setId] = useState<number | null>(null);
  const [refresh, setRefresh] = useState(0);
  const recent = useQuery<Summary[]>(api, "/orders", refresh);
  const current = useQuery<Order>(api, id ? `/orders/${id}` : null, refresh);
  return <section><h1>Manual orders</h1>
    <label>Recent orders<select value={id ?? ""} onChange={e => setId(Number(e.target.value) || null)}>
      <option value="">New order</option>
      {recent.data?.map(o => <option key={o.id} value={o.id}>#{o.id} · {o.customer_code} · {o.status.replaceAll("_", " ")}</option>)}
    </select></label>
    {(recent.error || current.error) && <p role="alert">{recent.error || current.error} <button onClick={() => setRefresh(r => r + 1)}>Reload</button></p>}
    {(!id || current.data) && <OrderForm key={`${id}-${refresh}`} api={api} order={current.data}
      onSaved={o => { setId(o.id); setRefresh(r => r + 1); }} />}
    {id && !current.data && !current.error && <p role="status">Loading order…</p>}
  </section>;
}
