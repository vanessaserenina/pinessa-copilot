import { useState } from "react";
import type { FormEvent } from "react";
import { errorMessage, jsonPost, useQuery } from "./api";
import type { Api, Bom, Product, ProductPage } from "./api";
import { QueryError } from "./Catalogue";

const MAX_LINES = 100;
const SEARCH_LIMIT = 20;

type DraftLine = { product: Product; quantity: string; scrap: string };

export function DraftBomForm({ api, product, onCancel, onCreated }: {
  api: Api;
  product: Product;
  onCancel: () => void;
  onCreated: (bom: Bom) => void;
}) {
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const params = new URLSearchParams({ q: search, limit: String(SEARCH_LIMIT) });
  const matches = useQuery<ProductPage>(api, search ? `/products?${params}` : null);
  const candidates = matches.data?.items.filter((candidate) => (
    candidate.id !== product.id && !lines.some((line) => line.product.id === candidate.id)
  ));

  function findComponents(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearch(String(new FormData(event.currentTarget).get("component")).trim());
  }

  function updateLine(id: number, field: "quantity" | "scrap", value: string) {
    setLines((current) => current.map((line) => line.product.id === id ? { ...line, [field]: value } : line));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!lines.length) return;
    const notes = String(new FormData(event.currentTarget).get("notes")).trim();
    setPending(true);
    setError("");
    try {
      const bom = await api<Bom>(`/products/${product.id}/boms`, jsonPost({
        notes: notes || null,
        items: lines.map((line) => ({
          component_product_id: line.product.id,
          quantity_per_unit: line.quantity,
          scrap_percent: line.scrap,
        })),
      }));
      onCreated(bom);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="draft-form">
      <h3>New BOM draft</h3>
      <form onSubmit={findComponents} className="inline-form" role="search" aria-label="Find BOM components">
        <label>Find a component<input type="search" name="component" maxLength={100} placeholder="Enter a SKU or name" required /></label>
        <button className="secondary" disabled={pending || lines.length >= MAX_LINES}>Find components</button>
      </form>
      {matches.error && <QueryError error={matches.error} retry={matches.retry} />}
      {search && !matches.data && !matches.error && <p role="status">Searching components…</p>}
      {candidates && candidates.length === 0 && <p className="muted" role="status">No available components in these results.</p>}
      {candidates && candidates.length > 0 && <ul className="component-matches">
        {candidates.map((candidate) => <li key={candidate.id}>
          <span>{candidate.name}<small className="cell-note">{candidate.sku} · {candidate.unit_of_measure}</small></span>
          <button className="secondary" disabled={pending || lines.length >= MAX_LINES}
            aria-label={`Add ${candidate.sku}`} onClick={() => setLines((current) => [...current, { product: candidate, quantity: "1", scrap: "0" }])}>Add</button>
        </li>)}
      </ul>}
      {matches.data && matches.data.total > SEARCH_LIMIT && <p className="muted">First {SEARCH_LIMIT} matches shown. Refine the search to find other SKUs.</p>}

      <form onSubmit={save}>
        <fieldset disabled={pending}>
          <legend>Draft components</legend>
          {lines.length === 0 && <p className="muted">Search for a product to add the first component.</p>}
          {lines.length === MAX_LINES && <p className="muted">This draft has the maximum of {MAX_LINES} components.</p>}
          {lines.map(({ product: component, quantity, scrap }) => <div className="draft-line" key={component.id}>
            <span>{component.name}<small className="cell-note">{component.sku} · {component.unit_of_measure}</small></span>
            <label>Qty per unit<input type="number" min="0.0001" max="99999999.9999" step="0.0001" value={quantity} required
              aria-label={`Quantity per unit for ${component.sku}`} onChange={(event) => updateLine(component.id, "quantity", event.target.value)} /></label>
            <label>Scrap %<input type="number" min="0" max="100" step="0.01" value={scrap} required
              aria-label={`Scrap percent for ${component.sku}`} onChange={(event) => updateLine(component.id, "scrap", event.target.value)} /></label>
            <button className="text-button" type="button" aria-label={`Remove ${component.sku}`}
              onClick={() => setLines((current) => current.filter((line) => line.product.id !== component.id))}>Remove</button>
          </div>)}
          <label>Notes (optional)<textarea name="notes" rows={2} maxLength={500} /></label>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="actions">
            <button className="primary" disabled={lines.length === 0}>{pending ? "Saving…" : "Save draft"}</button>
            <button className="secondary" type="button" onClick={onCancel}>Cancel</button>
          </div>
        </fieldset>
      </form>
    </div>
  );
}
