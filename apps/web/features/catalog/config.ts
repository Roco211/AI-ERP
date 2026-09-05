export type Field = {
  key: string;
  label: string;
  kind?:
    | "text"
    | "textarea"
    | "decimal"
    | "number"
    | "reference"
    | "select"
    | "attributes"
    | "attribute_schema";
  required?: boolean;
  reference?: string;
  options?: { value: string; label: string }[];
  hint?: string;
};
export type ResourceConfig = {
  title: string;
  singular: string;
  permission: string;
  description: string;
  fields: Field[];
  columns: string[];
};
const basics: Field[] = [
  { key: "code", label: "编码", required: true },
  { key: "name", label: "名称", required: true },
  { key: "notes", label: "备注", kind: "textarea" },
];
export const configs: Record<string, ResourceConfig> = {
  categories: {
    title: "商品分类",
    singular: "分类",
    permission: "catalog",
    description: "按品类组织商品，定义五金规格属性。",
    fields: [
      ...basics,
      {
        key: "parent_id",
        label: "上级分类",
        kind: "reference",
        reference: "categories",
      },
      {
        key: "attribute_schema",
        label: "规格属性模板",
        kind: "attribute_schema",
      },
    ],
    columns: ["code", "name", "parent_id"],
  },
  brands: {
    title: "品牌",
    singular: "品牌",
    permission: "catalog",
    description: "统一维护商品品牌。",
    fields: basics,
    columns: ["code", "name", "notes"],
  },
  units: {
    title: "计量单位",
    singular: "单位",
    permission: "catalog",
    description: "维护个、包、箱等单位；换算关系在每个商品中单独设置。",
    fields: basics,
    columns: ["code", "name", "notes"],
  },
};
const ref = (
  key: string,
  label: string,
  reference: string,
  required = false,
): Field => ({ key, label, reference, required, kind: "reference" });
const contacts: Field[] = [
  ...basics,
  { key: "contact", label: "联系人" },
  { key: "phone", label: "电话" },
  { key: "email", label: "邮箱" },
  { key: "address", label: "地址" },
];
Object.assign(configs, {
  customers: {
    title: "客户",
    singular: "客户",
    permission: "customer",
    description: "维护客户联系方式和默认价格等级。",
    fields: [
      ...contacts,
      {
        key: "price_tier",
        label: "价格等级",
        kind: "select",
        required: true,
        options: [
          { value: "standard", label: "标准价" },
          { value: "retail", label: "零售价" },
          { value: "wholesale", label: "批发价" },
        ],
      },
    ],
    columns: ["code", "name", "contact", "phone", "price_tier"],
  },
  suppliers: {
    title: "供应商",
    singular: "供应商",
    permission: "supplier",
    description: "维护供应商联系方式与供货关系。",
    fields: contacts,
    columns: ["code", "name", "contact", "phone"],
  },
  warehouses: {
    title: "仓库",
    singular: "仓库",
    permission: "warehouse",
    description: "维护仓库基础资料。",
    fields: [...basics, { key: "address", label: "地址" }],
    columns: ["code", "name", "address"],
  },
  products: {
    title: "商品资料",
    singular: "商品",
    permission: "catalog",
    description: "维护 SKU、规格属性、基础单位与补货参数。",
    fields: [
      { key: "sku", label: "商品编码", required: true },
      { key: "barcode", label: "条码" },
      { key: "name", label: "商品名称", required: true },
      { key: "short_name", label: "简称" },
      ref("category_id", "商品分类", "categories", true),
      ref("brand_id", "品牌", "brands"),
      { key: "model", label: "型号" },
      { key: "specification", label: "规格" },
      { key: "attributes", label: "规格属性", kind: "attributes" },
      ref("base_unit_id", "基础单位", "units", true),
      {
        ...ref("default_purchase_unit_id", "默认采购单位", "units"),
        hint: "留空使用基础单位。其他单位需先保存商品并设置单位换算。",
      },
      ref("default_sales_unit_id", "默认销售单位", "units"),
      { key: "min_stock_qty", label: "最低库存数量", kind: "decimal" },
      { key: "reorder_qty", label: "建议补货数量", kind: "decimal" },
      ref("preferred_supplier_id", "首选供应商", "suppliers"),
      { key: "notes", label: "备注", kind: "textarea" },
    ],
    columns: ["sku", "name", "specification", "category_id", "base_unit_id"],
  },
  "product-units": {
    title: "商品单位换算",
    singular: "换算关系",
    permission: "catalog",
    description: "每种单位直接换算为基础单位。例如 1 箱 = 1000 个，填写 1000。",
    fields: [
      ref("product_id", "商品", "products", true),
      ref("unit_id", "单位", "units", true),
      {
        key: "unit_to_base_factor",
        label: "换算率",
        kind: "decimal",
        required: true,
      },
      { key: "notes", label: "备注" },
    ],
    columns: ["product_id", "unit_id", "unit_to_base_factor"],
  },
  "product-prices": {
    title: "商品价格",
    singular: "价格",
    permission: "product.price",
    description: "价格按基础单位填写；客户专属价优先于价格等级和标准价。",
    fields: [
      ref("product_id", "商品", "products", true),
      {
        key: "price_type",
        label: "价格类型",
        kind: "select",
        required: true,
        options: [
          { value: "standard", label: "标准价" },
          { value: "retail", label: "零售价" },
          { value: "wholesale", label: "批发价" },
          { value: "customer", label: "客户专属价" },
        ],
      },
      ref("customer_id", "客户", "customers"),
      { key: "price", label: "单价", kind: "decimal", required: true },
      { key: "notes", label: "备注" },
    ],
    columns: ["product_id", "price_type", "customer_id", "price"],
  },
  "supplier-products": {
    title: "供应商商品",
    singular: "供货关系",
    permission: "supplier",
    description: "关联供应商货号、采购单位和预计交货天数。",
    fields: [
      ref("supplier_id", "供应商", "suppliers", true),
      ref("product_id", "商品", "products", true),
      { key: "supplier_sku", label: "供应商货号", required: true },
      ref("purchase_unit_id", "采购单位", "units", true),
      { key: "lead_days", label: "预计交货天数", kind: "number" },
      { key: "notes", label: "备注" },
    ],
    columns: ["supplier_id", "product_id", "supplier_sku", "lead_days"],
  },
} satisfies Record<string, ResourceConfig>);
