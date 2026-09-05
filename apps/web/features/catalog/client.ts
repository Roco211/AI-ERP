import { api, ApiError } from "@/lib/api";
import type { components } from "@/generated/api/schema";
export type ViewRow = Record<string, unknown> &
  Pick<components["schemas"]["CategoryRead"], "id" | "active" | "version">;
type Result = { data?: unknown; error?: unknown; response: Response };
type ListArgs = {
  q?: string;
  page?: number;
  page_size?: number;
  active?: boolean;
  [key: string]: unknown;
};
type Client = {
  list: (args: ListArgs) => Promise<Result>;
  get: (id: string) => Promise<Result>;
  create: (body: unknown, key: string) => Promise<Result>;
  update: (id: string, body: unknown, key: string) => Promise<Result>;
  toggle: (
    id: string,
    active: boolean,
    version: number,
    key: string,
  ) => Promise<Result>;
};
export function unwrap<T = { items: ViewRow[]; total: number }>(
  result: Result,
): T {
  if (!result.response.ok || !result.data) {
    const error = result.error as
      | components["schemas"]["ProblemDetails"]
      | undefined;
    throw new ApiError(
      result.response.status,
      error?.detail ?? "操作失败，请重试",
      error?.request_id,
    );
  }
  return result.data as T;
}
export const resourceClients: Record<string, Client> = {
  categories: {
    list: (args) => api.GET("/api/v1/categories", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/categories/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/categories", {
        body: body as components["schemas"]["CategoryCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/categories/{record_id}", {
        body: body as components["schemas"]["CategoryUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/categories/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/categories/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  brands: {
    list: (args) => api.GET("/api/v1/brands", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/brands/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/brands", {
        body: body as components["schemas"]["BrandCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/brands/{record_id}", {
        body: body as components["schemas"]["BrandUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/brands/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/brands/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  units: {
    list: (args) => api.GET("/api/v1/units", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/units/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/units", {
        body: body as components["schemas"]["UnitCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/units/{record_id}", {
        body: body as components["schemas"]["UnitUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/units/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/units/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  products: {
    list: (args) => api.GET("/api/v1/products", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/products/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/products", {
        body: body as components["schemas"]["ProductCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/products/{record_id}", {
        body: body as components["schemas"]["ProductUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/products/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/products/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  "product-units": {
    list: (args) =>
      api.GET("/api/v1/product-units", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/product-units/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/product-units", {
        body: body as components["schemas"]["ProductUnitCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/product-units/{record_id}", {
        body: body as components["schemas"]["ProductUnitUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/product-units/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/product-units/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  "product-prices": {
    list: (args) =>
      api.GET("/api/v1/product-prices", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/product-prices/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/product-prices", {
        body: body as components["schemas"]["ProductPriceCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/product-prices/{record_id}", {
        body: body as components["schemas"]["ProductPriceUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/product-prices/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/product-prices/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  customers: {
    list: (args) => api.GET("/api/v1/customers", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/customers/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/customers", {
        body: body as components["schemas"]["CustomerCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/customers/{record_id}", {
        body: body as components["schemas"]["CustomerUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/customers/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/customers/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  suppliers: {
    list: (args) => api.GET("/api/v1/suppliers", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/suppliers/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/suppliers", {
        body: body as components["schemas"]["SupplierCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/suppliers/{record_id}", {
        body: body as components["schemas"]["SupplierUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/suppliers/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/suppliers/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  "supplier-products": {
    list: (args) =>
      api.GET("/api/v1/supplier-products", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/supplier-products/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/supplier-products", {
        body: body as components["schemas"]["SupplierProductCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/supplier-products/{record_id}", {
        body: body as components["schemas"]["SupplierProductUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/supplier-products/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/supplier-products/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
  warehouses: {
    list: (args) => api.GET("/api/v1/warehouses", { params: { query: args } }),
    get: (id) =>
      api.GET("/api/v1/warehouses/{record_id}", {
        params: { path: { record_id: id } },
      }),
    create: (body, key) =>
      api.POST("/api/v1/warehouses", {
        body: body as components["schemas"]["WarehouseCreate"],
        params: { header: { "Idempotency-Key": key } },
      }),
    update: (id, body, key) =>
      api.PUT("/api/v1/warehouses/{record_id}", {
        body: body as components["schemas"]["WarehouseUpdate"],
        params: { path: { record_id: id }, header: { "Idempotency-Key": key } },
      }),
    toggle: (id, active, version, key) =>
      active
        ? api.POST("/api/v1/warehouses/{record_id}/activate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          })
        : api.POST("/api/v1/warehouses/{record_id}/deactivate", {
            body: { expected_version: version },
            params: {
              path: { record_id: id },
              header: { "Idempotency-Key": key },
            },
          }),
  },
};
