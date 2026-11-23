import { useEffect, useState } from "react";

export type User = { id: number; full_name: string; role: string };

export type Api = <T>(path: string, options?: RequestInit) => Promise<T>;

export type Product = {
  id: number;
  sku: string;
  name: string;
  product_type: "FINISHED" | "COMPONENT";
  unit_of_measure: string;
  source: "catalogue" | "synthetic";
  is_active: boolean;
  family_name: string;
  section_name: string;
  category_name: string;
  is_custom_allowed: boolean;
  has_active_bom: boolean;
};

export type ProductPage = { items: Product[]; total: number; limit: number; offset: number };

type BomStatus = "draft" | "active" | "retired";

export type BomSummary = {
  id: number;
  version_number: number;
  status: BomStatus;
  effective_from: string | null;
  line_count: number;
};

type BomItem = {
  component_product_id: number;
  component_sku: string;
  component_name: string;
  unit_of_measure: string;
  quantity_per_unit: string;
  scrap_percent: string;
};

export type Bom = Omit<BomSummary, "line_count"> & {
  notes: string | null;
  is_synthetic: boolean;
  items: BomItem[];
};

export type ProductDetail = Product & {
  active_bom: Bom | null;
  boms: BomSummary[];
  used_in_active_boms: number;
};

export type Calculation = {
  bom_version: number;
  quantity: number;
  lines: (BomItem & { required: string; exact_required: string })[];
};

export class ApiError extends Error {
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }

  readonly status: number;
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Try again.";
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, options);
  } catch (error) {
    if (options?.signal?.aborted) throw error;
    throw new Error("Could not reach the server. Try again.", { cause: error });
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    let message = `The request failed (HTTP ${response.status}). Try again.`;
    if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (Array.isArray(body?.detail)) {
      message = body.detail.map(({ loc, msg }: { loc: (string | number)[]; msg: string }) => (
        `${loc.slice(1).map((part) => String(part).replaceAll("_", " ")).join(" · ")}: ${msg}`
      )).join(". ");
    }
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) return undefined as T;
  if (new Headers(options?.headers).get("Accept") === "application/octet-stream") return response.blob() as Promise<T>;
  return response.json() as Promise<T>;
}

export function jsonPost(body: object): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

type QueryResult<T> = { data: T; error?: never } | { data?: never; error: string };

export function useQuery<T>(api: Api, path: string | null, revision = 0) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ path: string; attempt: number; revision: number; value: QueryResult<T> }>();

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();

    api<T>(path, { signal: controller.signal }).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ path, attempt, revision, value: { data } });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({ path, attempt, revision, value: { error: errorMessage(error) } });
      },
    );

    return () => controller.abort();
  }, [api, path, attempt, revision]);

  const current = result?.path === path && result.attempt === attempt && result.revision === revision ? result.value : undefined;
  return { data: current?.data, error: current?.error, retry: () => setAttempt((value) => value + 1) };
}

export function decimalText(value: string) {
  if (!value.includes(".") || /e/i.test(value)) return value;
  return value.replace(/0+$/, "").replace(/\.$/, "");
}
