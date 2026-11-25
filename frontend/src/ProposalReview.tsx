import { useState } from "react";
import type { FormEvent } from "react";
import { errorMessage, jsonPost, useQuery } from "./api";
import type { Api, ProductPage } from "./api";

type Source = { id: number; raw_text: string | null; original_filename: string | null };
type Line = { requested_text: string; stated_sku: string | null; quantity: string | null; unit: string | null;
  specification: string | null; customization_notes: string | null };
type Candidate = { product_id: number; sku: string; name: string; unit: string; method: string; attributes?: Record<string, unknown> };
type Match = { source_quantity: { quantity: string; unit: string; normalized_unit: string; source_clause: string } | null;
  selected_product_id: number | null; reason: string | null; candidates: Candidate[] };
type Proposal = { confirmed_order_id: number | null; error_message: string | null; line_matches: Match[] | null;
  review_headers?: { customer_name: string | null; po_number: string | null; check_po: boolean } | null;
  validated_output: { customer: Record<string, string | null> | null; customer_po_number: string | null;
    requested_delivery_date: string | null; lines: Line[] } | null };
const customerFields = ["name", "contact_name", "email", "phone", "delivery_address"];

function ReviewLine({ api, line, match, index }: { api: Api; line: Line; match: Match; index: number }) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(match.candidates.find(c => c.product_id === match.selected_product_id));
  const found = useQuery<ProductPage>(api, search.trim()
    ? "/products?product_type=FINISHED&limit=20&q=" + encodeURIComponent(search.trim()) : null);
  const choices = new Map(match.candidates.map(c => [c.product_id, c]));
  found.data?.items.filter(p => p.is_active).forEach(p => choices.set(p.id, {
    product_id: p.id, sku: p.sku, name: p.name, unit: p.unit_of_measure, method: "search",
  }));
  if (selected) choices.set(selected.product_id, selected);
  const evidence = match.source_quantity;
  const initialQuantity = evidence && selected
    && [evidence.normalized_unit, evidence.unit.toLowerCase()].includes(selected.unit.toLowerCase())
    && /^\d+(?:\.0+)?$/.test(evidence.quantity) && Number(evidence.quantity) >= 1
    && Number(evidence.quantity) <= 10000000 ? evidence.quantity : "";
  return <fieldset><legend>Line {index + 1}</legend>
    <p>{evidence?.source_clause ?? line.requested_text}</p>
    {line.stated_sku && <p>{line.stated_sku}</p>}
    <p>{evidence ? `Requested quantity: ${evidence.quantity} ${evidence.unit}` : "Check the source and enter the quantity agreed with the customer."}</p>
    {match.reason && <p className="notice">{match.reason}</p>}
    <label>Search catalogue<input value={search} onChange={e => setSearch(e.target.value)} maxLength={100} /></label>
    {found.error && <p role="alert">{found.error}</p>}
    <label>Product<select aria-label={`Product for line ${index + 1}`} required name={`product-${index}`} value={selected?.product_id ?? ""}
      onChange={e => setSelected(choices.get(Number(e.target.value)))}>
      <option value="">Select a product</option>
      {[...choices.values()].map(c => <option value={c.product_id} key={c.product_id}>{c.sku} · {c.name} · {c.unit} ({c.method})</option>)}
    </select></label>
    {selected?.attributes && Object.keys(selected.attributes).length > 0 && <dl>
      {Object.entries(selected.attributes).map(([key, value]) => <div className="dashboard-count" key={key}>
        <dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>}
    <label>Confirmed quantity {selected && `(${selected.unit})`}<input type="number" required min="1" max="10000000"
      name={`quantity-${index}`} key={selected?.product_id ?? "none"}
      step="1" defaultValue={initialQuantity} /></label>
    <label>Unit price<input name={`price-${index}`} type="number" required min="0" step="0.0001" /></label>
    <label>Specification<input name={`spec-${index}`} defaultValue={line.specification ?? ""} /></label>
    <label>Customization notes<input name={`notes-${index}`} defaultValue={line.customization_notes ?? ""} /></label>
  </fieldset>;
}

export function ProposalReview({ api }: { api: Api }) {
  const [source, setSource] = useState<Source>();
  const [proposal, setProposal] = useState<Proposal>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [orderId, setOrderId] = useState<number | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [queueRevision, setQueueRevision] = useState(0);
  const [queuePages, setQueuePages] = useState([0]);
  const pending = useQuery<number[]>(api, `/order-sources/pending-review?after_id=${queuePages[queuePages.length - 1]}`, queueRevision);
  const nextCursor = pending.data?.length === 100 ? pending.data[99] : null;
  const output = proposal?.validated_output;

  async function load(event: FormEvent<HTMLFormElement>, existing: boolean) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true); setError(""); setProposal(undefined); setSource(undefined); setOrderId(null); setDuplicate(false);
    try {
      let id = sourceId;
      if (!existing) {
        const file = fields.get("file") as File;
        if (file?.name && String(fields.get("text")).trim()) throw new Error("Choose a file or paste a message.");
        fields.delete(file?.name ? "text" : "file");
        const uploaded = await api<Source>("/order-sources", { method: "POST", body: fields });
        id = String(uploaded.id); setSourceId(id);
      }
      const path = "/order-sources/" + id;
      const original = await api<Source>(path + "/extract-text", { method: "POST" });
      setSource(original);
      const result = await api<Proposal>(path + "/extract-order", { method: "POST" });
      setQueueRevision(r => r + 1); setProposal(result); setOrderId(result.confirmed_order_id); setError(result.error_message ?? "");
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  async function download() {
    if (!source?.original_filename) return;
    try {
      const blob = await api<Blob>(`/order-sources/${source.id}/file`, { headers: { Accept: "application/octet-stream" } });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = source.original_filename; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) { setError(errorMessage(err)); }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!output || !source) return;
    const fields = new FormData(event.currentTarget);
    const value = (key: string) => String(fields.get(key) ?? "").trim() || null;
    const code = value("code");
    setBusy(true); setError("");
    try {
      const result = await api<{ id: number }>(`/order-sources/${source.id}/confirm`, jsonPost({
        customer: { ...Object.fromEntries(customerFields.map(k => [k, value(k)])), code },
        customer_code: code, currency: value("currency"), customer_po_number: value("po"),
        requested_delivery_date: value("date"), acknowledge_duplicate: fields.has("acknowledge"),
        lines: output.lines.map((_, i) => ({ product_id: Number(value(`product-${i}`)),
          quantity: Number(value(`quantity-${i}`)), unit_price: value(`price-${i}`),
          specification: value(`spec-${i}`), customization_notes: value(`notes-${i}`) })),
      }));
      setOrderId(result.id); setQueueRevision(r => r + 1);
    } catch (err) { const message = errorMessage(err); setError(message); setDuplicate(message.startsWith("Possible duplicate")); }
    finally { setBusy(false); }
  }

  return <section><h1>Order intake</h1>
    <form className="panel" onSubmit={e => load(e, false)}><fieldset disabled={busy}>
      <label>Order message<textarea name="text" /></label>
      <label>Order file<input type="file" name="file" accept=".pdf,.xlsx,.csv,.png,.jpg,.jpeg" /></label>
      <button>Extract proposal</button></fieldset></form>
    <form className="actions" onSubmit={e => load(e, true)}>
      <label>Awaiting review<select value={pending.data?.includes(Number(sourceId)) ? sourceId : ""}
        onChange={e => setSourceId(e.target.value)}>
        <option value="">Select a source</option>
        {pending.data?.map(id => <option key={id} value={id}>Source #{id}</option>)}
      </select></label>
      {queuePages.length > 1 && <button type="button" onClick={() => setQueuePages(p => p.slice(0, -1))}>Previous page</button>}
      {nextCursor !== null && <button type="button"
        onClick={() => setQueuePages([...queuePages, nextCursor])}>Next page</button>}
      {pending.error && <span role="alert">{pending.error} <button type="button" onClick={pending.retry}>Retry queue</button></span>}
      <label>Existing source number<input type="number" min="1" required value={sourceId} onChange={e => setSourceId(e.target.value)} /></label>
      <button disabled={busy}>Open source</button></form>
    {busy && <p role="status">Processing…</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {orderId && <p role="status">Confirmed order #{orderId}. <a href="#/orders">View orders</a></p>}
    {source && <div className="proposal-review"><section className="panel"><h2>Source #{source.id}</h2>
      {source.original_filename && <button onClick={download}>Download {source.original_filename}</button>}
      <pre>{source.raw_text}</pre></section>
      {output && proposal?.line_matches && !orderId && <form className="panel" onSubmit={confirm}><h2>Review proposal</h2>
        <fieldset disabled={busy}><label>Customer code<input name="code" required defaultValue={output.customer?.code ?? ""} /></label>
          {customerFields.map(k => <label key={k}>{k.replaceAll("_", " ")}<input name={k}
            defaultValue={(k === "name" && proposal.review_headers ? proposal.review_headers.customer_name : output.customer?.[k]) ?? ""} /></label>)}
          {proposal.review_headers?.check_po && <p className="notice">Check the PO number against the original source and enter it before confirmation.</p>}
          <label>PO number<input name="po" required={proposal.review_headers?.check_po}
            defaultValue={(proposal.review_headers ? proposal.review_headers.po_number : output.customer_po_number) ?? ""} /></label>
          <label>Delivery date<input type="date" name="date" defaultValue={output.requested_delivery_date ?? ""} /></label>
          <label>Currency<input name="currency" required pattern="[A-Z]{3}" placeholder="SGD" /></label>
          {output.lines.map((line, i) => <ReviewLine key={i} api={api} line={line} match={proposal.line_matches![i]} index={i} />)}
          {duplicate && <label><input type="checkbox" name="acknowledge" required />I reviewed the possible duplicate.</label>}
          <button className="primary" disabled={!output.lines.length}>Confirm order and reserve stock</button>
        </fieldset></form>}</div>}
  </section>;
}
