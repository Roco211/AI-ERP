export const replenishmentReadPermissions = [
  "replenishment.read",
  "catalog.read",
  "inventory.read",
  "sales.read",
  "purchase.read",
  "supplier.read",
] as const;

export function hasEvery(permissions: string[], required: readonly string[]) {
  return required.every((permission) => permissions.includes(permission));
}

export const replenishmentCreatePermissions = [
  ...replenishmentReadPermissions,
  "replenishment.create",
  "purchase.order.write",
  "product.cost.read",
] as const;
