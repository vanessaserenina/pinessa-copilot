import { useState } from "react";
import type { FormEvent } from "react";
import { decimalText, errorMessage, jsonPost, useQuery } from "./api";
import type { Api, Bom, Calculation, ProductDetail } from "./api";
import { ProductType, QueryError } from "./Catalogue";
import { DraftBomForm } from "./DraftBomForm";

function ComponentTable({ lines }: { lines: Bom["items"] | Calculation["lines"] }) {
  const isCalculation = lines.some((line) => "required" in line);

  return <div className="table-scroll"><table>
    <thead><tr>
      <th scope="col">Component</th><th scope="col">Unit</th><th scope="col" className="numeric">Qty per unit</th>
      <th scope="col" className="numeric">Scrap</th>
      {isCalculation && <><th scope="col" className="numeric">Before rounding</th><th scope="col" className="numeric">Required</th></>}
    </tr></thead>
    <tbody>{lines.map((line) => <tr key={line.component_product_id}>
      <td><strong>{line.component_name}</strong><small className="cell-note sku">{line.component_sku}</small></td>
      <td>{line.unit_of_measure}</td>
      <td className="numeric">{decimalText(line.quantity_per_unit)}</td>
      <td className="numeric">{decimalText(line.scrap_percent)}%</td>
      {"required" in line && <><td className="numeric">{decimalText(line.exact_required)}</td>
        <td className="numeric"><strong>{decimalText(line.required)}</strong></td></>}
    </tr>)}</tbody>
  </table></div>;
}

function Calculator({ api, product }: { api: Api; product: ProductDetail }) {
  const [result, setResult] = useState<Calculation>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function calculate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const quantity = Number(new FormData(event.currentTarget).get("quantity"));
    setResult(undefined);
    setError("");
    setPending(true);
    try {
      setResult(await api<Calculation>(`/products/${product.id}/bom-calculation`, jsonPost({ quantity })));
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return <section className="panel" aria-labelledby="requirements-heading">
    <div className="detail-panel-heading">
      <h2 id="requirements-heading">Component requirements</h2>
      {product.active_bom ? <form className="inline-form" onSubmit={calculate}>
        <label>Product quantity ({product.unit_of_measure})<input type="number" min={1} max={10_000_000} step={1} name="quantity" defaultValue="1" required disabled={pending} /></label>
        <button className="primary" disabled={pending}>{pending ? "Calculating…" : "Calculate"}</button>
      </form> : <p className="muted">This product has no active BOM.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {result && <p className="calculation-summary" role="status">BOM v{result.bom_version} · Requirements for {result.quantity.toLocaleString()} {product.unit_of_measure}</p>}
    </div>
    {result && <ComponentTable lines={result.lines} />}
  </section>;
}

type VersionAction = { kind: "activate" | "delete"; id: number; version: number };

export function ProductDetailScreen({ id, api, isAdmin, onBomChange }: {
  id: string;
  api: Api;
  isAdmin: boolean;
  onBomChange: () => void;
}) {
  const { data: product, error, retry } = useQuery<ProductDetail>(api, `/products/${id}`);
  const [versionId, setVersionId] = useState<number>();
  const [editing, setEditing] = useState(false);
  const [action, setAction] = useState<VersionAction>();
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; error?: boolean }>();
  const version = product?.boms.find((bom) => bom.id === (versionId ?? product.active_bom?.id)) ?? product?.boms[0];
  const active = product?.active_bom;
  const versionQuery = useQuery<Bom>(api, version && version.id !== active?.id ? `/boms/${version.id}` : null);
  const bom = version?.id === active?.id ? active : versionQuery.data;

  async function changeVersion(selectedAction: VersionAction) {
    const { id, kind, version } = selectedAction;
    setPending(true);
    setFeedback(undefined);
    try {
      if (kind === "activate") {
        await api<Bom>(`/boms/${id}/activate`, { method: "POST" });
      } else {
        await api<void>(`/boms/${id}`, { method: "DELETE" });
      }
      setVersionId(kind === "activate" ? id : undefined);
      setAction(undefined);
      setFeedback({ message: `BOM v${version} ${kind === "activate" ? "activated" : "deleted"}.` });
      retry();
      onBomChange();
    } catch (error) {
      setFeedback({ message: errorMessage(error), error: true });
    } finally {
      setPending(false);
    }
  }

  return <>
    <a className="back-link" href="#/products">← Back to catalogue</a>
    {error && <QueryError error={error} retry={retry} />}
    {!product && !error && <p role="status">Loading product…</p>}
    {product && <>
      <div className="page-heading product-heading">
        <div className="product-subtitle"><span className="sku">{product.sku}</span><ProductType product={product} />{!product.is_active && <span className="badge retired">Inactive</span>}</div>
        <h1>{product.name}</h1>
        <p>{product.section_name === product.category_name ? product.section_name : `${product.section_name} / ${product.category_name}`}</p>
      </div>
      <dl className="panel metadata">
        <div><dt>Family</dt><dd>{product.family_name}</dd></div>
        <div><dt>Unit</dt><dd>{product.unit_of_measure}</dd></div>
        <div><dt>Source</dt><dd className="capitalize">{product.source === "catalogue" ? "Catalogue" : "Sample records"}</dd></div>
        <div><dt>Custom orders</dt><dd>{product.is_custom_allowed ? "Allowed" : "Unavailable"}</dd></div>
        {product.product_type === "COMPONENT" && <div><dt>Used in active BOMs</dt><dd>{product.used_in_active_boms}</dd></div>}
      </dl>

      {product.product_type === "FINISHED" && <>
        <Calculator key={active?.id ?? "none"} api={api} product={product} />
        <section className="panel" aria-labelledby="versions-heading">
          <div className="detail-panel-heading">
            <div className="split-heading"><h2 id="versions-heading">BOM versions</h2>
              {isAdmin && product.is_active && !editing && <button className="secondary" disabled={Boolean(action)} onClick={() => setEditing(true)}>New BOM draft</button>}
            </div>
            {feedback && <p className={feedback.error ? "error" : "notice"} role={feedback.error ? "alert" : "status"}>{feedback.message}</p>}
            {editing && <DraftBomForm api={api} product={product} onCancel={() => setEditing(false)} onCreated={(draft) => {
              setEditing(false);
              setVersionId(draft.id);
              setFeedback({ message: `BOM v${draft.version_number} saved as a draft.` });
              retry();
            }} />}
            {version ? <>
              <label className="version-select">Version<select value={version.id} disabled={Boolean(action) || editing} onChange={(event) => {
                setVersionId(Number(event.target.value));
                setFeedback(undefined);
              }}>
                {product.boms.map((item) => <option key={item.id} value={item.id}>v{item.version_number} · {item.status} · {item.line_count} {item.line_count === 1 ? "component" : "components"}</option>)}
              </select></label>
              {bom && <div className="version-meta"><span className={`badge ${bom.status}`}>{bom.status}</span>
                {bom.effective_from && <span>Effective {bom.effective_from}</span>}
              </div>}
              {bom?.notes && <p className="bom-notes">{bom.notes}</p>}
              {isAdmin && version.status === "draft" && !action && !editing && <div className="actions">
                <button className="primary" disabled={!product.is_active} onClick={() => {
                  setFeedback(undefined);
                  setAction({ kind: "activate", id: version.id, version: version.version_number });
                }}>Activate version</button>
                <button className="secondary danger-text" onClick={() => {
                  setFeedback(undefined);
                  setAction({ kind: "delete", id: version.id, version: version.version_number });
                }}>Delete draft</button>
              </div>}
              {action && <div className="confirmation">
                <p>{action.kind === "delete" ? `Delete draft v${action.version}?` : `Activate v${action.version}?${active ? ` This will retire v${active.version_number}.` : ""}`}</p>
                <div className="actions">
                  <button className={action.kind === "delete" ? "danger" : "primary"} disabled={pending} onClick={() => void changeVersion(action)}>
                    {action.kind === "delete" ? "Confirm deletion" : "Confirm activation"}
                  </button>
                  <button className="secondary" disabled={pending} onClick={() => { setAction(undefined); setFeedback(undefined); }}>Cancel</button>
                </div>
              </div>}
              {versionQuery.error && <QueryError error={versionQuery.error} retry={versionQuery.retry} />}
              {!bom && !versionQuery.error && <p role="status">Loading version…</p>}
            </> : !editing && <p className="muted">No BOM versions have been created for this product.</p>}
          </div>
          {bom && (bom.items.length > 0 ? <ComponentTable lines={bom.items} /> : <p className="panel-message">This version contains no components.</p>)}
        </section>
      </>}
    </>}
  </>;
}
