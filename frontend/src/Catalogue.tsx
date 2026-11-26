import { useState } from "react";
import type { FormEvent } from "react";
import { useQuery } from "./api";
import type { Api, Product, ProductPage } from "./api";

const PAGE_SIZE = 20;

export function ProductType({ product }: { product: Product }) {
  return <span className="badge neutral">{product.product_type === "FINISHED" ? "Finished product" : "Component"}</span>;
}

export function QueryError({ error, retry }: { error: string; retry: () => void }) {
  return <div className="query-error"><p className="error" role="alert">{error}</p><button className="secondary" onClick={retry}>Retry</button></div>;
}

export function Catalogue({ api, revision }: { api: Api; revision: number }) {
  const [filters, setFilters] = useState(new URLSearchParams());
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams(filters);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(offset));
  const { data, error, retry } = useQuery<ProductPage>(api, `/products?${params}`, revision);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const selected = new URLSearchParams();
    for (const [name, value] of new FormData(event.currentTarget)) {
      const text = String(value).trim();
      if (text) selected.set(name, text);
    }
    setFilters(selected);
    setOffset(0);
  }

  return (
    <>
      <div className="page-heading">
        <h1>Product catalogue</h1>
        <p>Browse finished products and components by SKU, product name or family.</p>
      </div>
      <form className="panel search-panel" role="search" aria-label="Product catalogue search" onSubmit={search} onReset={() => {
        setFilters(new URLSearchParams());
        setOffset(0);
      }}>
        <div className="catalogue-fields">
          <label>Search products<input type="search" name="q" maxLength={100} placeholder="Enter a SKU, name or family" /></label>
          <label>Product type<select name="product_type"><option value="">All types</option><option value="FINISHED">Finished products</option><option value="COMPONENT">Components</option></select></label>
          <label>Active BOM<select name="has_bom"><option value="">Any</option><option value="true">Has active BOM</option><option value="false">No active BOM</option></select></label>
          <button className="primary">Search</button>
        </div>
        <div className="filter-footer">
          <details>
            <summary>More filters</summary>
            <div className="extra-filters">
              <label>Source<select name="source"><option value="">All sources</option><option value="catalogue">Catalogue</option><option value="synthetic">Sample records</option></select></label>
              <label>Section code<input name="section" maxLength={80} /></label>
              <label>Group code<input name="group" maxLength={80} /></label>
              <label className="checkbox-label"><input type="checkbox" name="include_inactive" value="true" /> Include inactive</label>
            </div>
          </details>
          <button className="text-button" type="reset">Clear filters</button>
        </div>
      </form>

      <section className="panel results" aria-labelledby="products-heading">
        <div className="section-heading">
          <h2 id="products-heading">Products</h2>
          {data && <span className="muted" role="status">{data.total.toLocaleString()} {data.total === 1 ? "result" : "results"}</span>}
        </div>
        {error && <QueryError error={error} retry={retry} />}
        {!data && !error && <p className="panel-message" role="status">Loading products…</p>}
        {data && data.items.length === 0 && <p className="panel-message">{filters.size ? "No products match these filters." : "There are no products to show."}</p>}
        {data && data.items.length > 0 && <div className="table-scroll">
          <table>
            <thead><tr><th scope="col">SKU</th><th scope="col">Product</th><th scope="col">Type</th><th scope="col">Category</th><th scope="col">BOM</th></tr></thead>
            <tbody>{data.items.map((product) => <tr key={product.id}>
              <td><a className="sku" href={`#/products/${product.id}`}>{product.sku}</a></td>
              <td><strong>{product.name}</strong>{!product.is_active && <small className="cell-note">Inactive</small>}</td>
              <td><ProductType product={product} /></td>
              <td>{product.category_name}{product.section_name !== product.category_name && <small className="cell-note">{product.section_name}</small>}</td>
              <td>{product.has_active_bom ? <span className="badge active">Active</span> : <span className="muted">None</span>}</td>
            </tr>)}</tbody>
          </table>
        </div>}
        {data && (data.total > PAGE_SIZE || offset > 0) && <div className="pagination">
          <span>{data.items.length ? `${data.offset + 1}–${data.offset + data.items.length} of ${data.total}` : "No products on this page"}</span>
          <div className="actions">
            <button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
            <button className="secondary" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
          </div>
        </div>}
      </section>
    </>
  );
}
