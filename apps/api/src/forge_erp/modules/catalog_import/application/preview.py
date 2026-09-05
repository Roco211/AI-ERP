"""Resolve workbook columns through current catalog schemas and validation rules."""

import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import text

from forge_erp.core.errors import Problem
from forge_erp.core.security import fingerprint
from forge_erp.modules.audit.service import record_mutation
from forge_erp.modules.catalog.application.service import validate_record
from forge_erp.modules.catalog.infrastructure.resources import RESOURCES
from forge_erp.modules.catalog_import.domain.columns import COLUMNS, KEYS, require_write


def json_dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


def error(column, code, message):
    return {"column": column, "code": code, "message": message}


def validation_message(item):
    kind = item["type"]
    if kind == "missing":
        return "请填写必填字段"
    if kind.startswith("decimal"):
        return "数值必须有限，最多20位数字及6位小数，并符合字段范围"
    if kind in {"greater_than", "greater_than_equal", "less_than", "less_than_equal"}:
        return "数值超出字段允许范围；换算率须大于零，数量和价格不能为负"
    if kind == "literal_error":
        return "请使用模板说明列出的允许值"
    if kind.startswith("string"):
        return "文本长度或格式不符合资料规则，请检查必填值与编码字符"
    return "字段内容不符合资料规则"


class Resolver:
    def __init__(self, db, ctx):
        self.db, self.ctx, self.codes, self.ids = db, ctx, {}, {}

    async def code(self, table, code):
        key = (table, code)
        if key not in self.codes:
            field = "sku" if table == "products" else "code"
            row = (
                (
                    await self.db.execute(
                        text(
                            f"SELECT * FROM forge.{table} "
                            f"WHERE organization_id=:org AND {field}=:code"
                        ),
                        {"org": self.ctx.organization_id, "code": code},
                    )
                )
                .mappings()
                .first()
            )
            self.codes[key] = dict(row) if row else None
            if row:
                self.ids[(table, str(row["id"]))] = dict(row)
        return self.codes[key]

    async def id(self, table, id):
        key = (table, str(id))
        if key not in self.ids:
            row = (
                (
                    await self.db.execute(
                        text(f"SELECT * FROM forge.{table} WHERE organization_id=:org AND id=:id"),
                        {"org": self.ctx.organization_id, "id": id},
                    )
                )
                .mappings()
                .first()
            )
            self.ids[key] = dict(row) if row else None
        return self.ids[key]


def parse_value(cell, column):
    value = cell.value.strip()
    if value == "":
        return None if column.reference or column.field == "barcode" else ""
    if column.kind == "identifier":
        if cell.kind != "text":
            raise Problem(
                422, "UNSAFE_IDENTIFIER_CELL", "编码和条码必须是文本；不能恢复数字格式丢失的内容"
            )
        return value
    if column.kind in {"decimal", "integer"}:
        if cell.kind not in {"number", "text"}:
            raise Problem(422, "INVALID_DECIMAL", "数值不能是日期、布尔或错误单元格")
        try:
            number = Decimal(value)
            if not number.is_finite() or len(value) > 100:
                raise InvalidOperation
            if column.kind == "integer":
                if number != number.to_integral_value() or number.copy_abs() > 3650:
                    raise InvalidOperation
                return int(number)
            return number
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise Problem(422, "INVALID_DECIMAL", "请填写有效、有限且符合字段精度的数值") from exc
    if cell.kind in {"date", "error"}:
        raise Problem(422, "INVALID_CELL", "请使用文本或明确数值，不接受日期或错误单元格")
    if column.kind == "json":
        try:
            decoded = json.loads(
                value,
                parse_float=Decimal,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            pending, nodes = [(decoded, 0)], 0
            while pending:
                item, depth = pending.pop()
                nodes += 1
                if depth > 32 or nodes > 10000:
                    raise ValueError("Attribute template structure exceeds limits")
                if isinstance(item, (list, dict)):
                    children = item.values() if isinstance(item, dict) else item
                    pending.extend((child, depth + 1) for child in children)
            return decoded
        except (ValueError, TypeError, RecursionError) as exc:
            raise Problem(422, "INVALID_ATTRIBUTE", "属性模板必须为合法 JSON") from exc
    return (
        value.replace("＊", "×").replace("*", "×")
        if column.field in {"name", "short_name", "model", "specification"}
        else value
    )


async def prepare(db, ctx, resource, mode, sheet):
    require_write(ctx, resource)
    spec, columns = RESOURCES[resource], COLUMNS[resource]
    unknown = [
        header
        for header in sheet.headers
        if header not in columns and not (resource == "products" and header.startswith("属性:"))
    ]
    if unknown:
        raise Problem(422, "UNKNOWN_COLUMN", "不接受未知或系统字段列：" + "、".join(unknown)[:1000])
    needed = {label for label, item in columns.items() if item.required}
    if mode == "UPDATE_EXISTING":
        needed = {
            label
            for label, item in columns.items()
            if item.field in KEYS[resource] and item.field != "customer_id"
        }
    missing = needed - set(sheet.headers)
    if missing:
        raise Problem(422, "REQUIRED", "缺少模板列：" + "、".join(sorted(missing)))
    resolver = Resolver(db, ctx)
    prepared = []
    seen = {}
    for row_no, cells in sheet.rows:
        row = {
            "id": uuid4(),
            "row_no": row_no,
            "action": "CREATE" if mode == "CREATE_ONLY" else "UPDATE",
            "raw_values": {k: v.value for k, v in cells.items()},
            "cleaned_values": {},
            "errors": [],
            "command_values": {},
            "references_snapshot": [],
            "locator": {},
            "expected_target_id": None,
            "expected_version": None,
        }
        values, attrs = {}, {}
        for header, cell in cells.items():
            try:
                if header.startswith("属性:"):
                    attrs[header[3:]] = cell
                    row["cleaned_values"][header] = cell.value.strip()
                    continue
                column = columns[header]
                value = parse_value(cell, column)
                if column.field == "price_tier" and value == "" and mode == "CREATE_ONLY":
                    continue
                if column.kind in {"decimal", "integer", "json"} and value == "":
                    if mode == "CREATE_ONLY" and not column.required:
                        continue  # Create defaults appear in the final preview.
                    raise Problem(422, "REQUIRED", "已映射数值或模板字段不能为空")
                row["cleaned_values"][header] = (
                    str(value)
                    if isinstance(value, (Decimal, int))
                    else (json_dump(value) if isinstance(value, (list, dict)) else value)
                )
                if column.reference and value is not None:
                    reference = await resolver.code(column.reference, value)
                    if not reference or not reference["active"]:
                        raise Problem(409, "UNKNOWN_REFERENCE", "引用编码不存在或已停用：" + header)
                    value = reference["id"]
                values[column.field] = value
            except Problem as exc:
                row["errors"].append(error(header, exc.code, exc.detail))
        key_values = {key: values.get(key) for key in KEYS[resource]}
        row["locator"] = key_values
        duplicate_keys = []
        if all(key_values[k] is not None for k in KEYS[resource] if k != "customer_id"):
            duplicate_keys.append(("natural", fingerprint(key_values)))
        if resource == "products" and values.get("barcode"):
            duplicate_keys.append(("barcode", values["barcode"]))
        if (
            resource == "supplier-products"
            and values.get("supplier_id")
            and values.get("supplier_sku")
        ):
            duplicate_keys.append(
                ("supplier_sku", str(values["supplier_id"]), values["supplier_sku"])
            )
        for duplicate in duplicate_keys:
            if duplicate in seen:
                message = error("", "DUPLICATE_IN_FILE", "文件内定位键、条码或供应商货号重复")
                row["errors"].append(message)
                seen[duplicate]["errors"].append(message)
            else:
                seen[duplicate] = row
        if not row["errors"]:
            clauses = " AND ".join(f"{key} IS NOT DISTINCT FROM :{key}" for key in key_values)
            previous_row = (
                (
                    await db.execute(
                        text(
                            f"SELECT * FROM forge.{spec.table} "
                            f"WHERE organization_id=:org AND {clauses}"
                        ),
                        {"org": ctx.organization_id, **key_values},
                    )
                )
                .mappings()
                .first()
            )
            previous = dict(previous_row) if previous_row else None
            if mode == "CREATE_ONLY" and previous:
                row["errors"].append(
                    error("", "EXISTING_CODE", "定位键已存在；请明确选择仅更新模式后重新预览")
                )
            elif mode == "UPDATE_EXISTING" and not previous:
                row["errors"].append(
                    error("", "TARGET_NOT_FOUND", "仅更新模式的目标不存在，不会隐式新增")
                )
            if previous:
                row["expected_target_id"], row["expected_version"] = (
                    previous["id"],
                    previous["version"],
                )
                # Only schema fields, never permission-sensitive Product price projections.
                values = {key: previous[key] for key in spec.schema.model_fields} | values
            if resource == "products":
                category = await resolver.id("categories", values.get("category_id"))
                definitions = (
                    {item["key"]: item for item in category["attribute_schema"]} if category else {}
                )
                attributes = dict(values.get("attributes", {}))
                for key, cell in attrs.items():
                    definition = definitions.get(key)
                    if not definition:
                        row["errors"].append(
                            error("属性:" + key, "INVALID_ATTRIBUTE", "分类模板中没有该属性")
                        )
                        continue
                    value = cell.value.strip()
                    if definition["kind"] == "boolean":
                        if value in {"是", "true", "TRUE", "1"}:
                            attributes[key] = True
                        elif value in {"否", "false", "FALSE", "0"}:
                            attributes[key] = False
                        elif not value:
                            attributes.pop(key, None)
                        else:
                            row["errors"].append(
                                error("属性:" + key, "INVALID_ATTRIBUTE", "布尔属性请填写是/否")
                            )
                    else:
                        if definition["kind"] == "decimal" and cell.kind not in {"text", "number"}:
                            row["errors"].append(
                                error("属性:" + key, "INVALID_DECIMAL", "数值属性不能是日期或布尔")
                            )
                        attributes[key] = value.replace("＊", "×").replace("*", "×")
                    row["cleaned_values"]["属性:" + key] = attributes.get(key)
                values["attributes"] = attributes
            try:
                validated = spec.schema.model_validate(values).model_dump()
                await validate_record(
                    db, ctx, resource, validated, row["expected_target_id"], previous
                )
                row["command_values"] = validated
                # Show every final field, including preserved update values and schema defaults.
                for label, column in columns.items():
                    value = validated.get(column.field)
                    if column.reference and value is not None:
                        ref = await resolver.id(column.reference, value)
                        value = (
                            ref["sku" if column.reference == "products" else "code"]
                            if ref
                            else None
                        )
                    elif isinstance(value, (dict, list)):
                        value = json_dump(value)
                    elif value is not None and not isinstance(value, bool):
                        value = str(value)
                    row["cleaned_values"][label] = value
                for key, value in validated.get("attributes", {}).items():
                    row["cleaned_values"]["属性:" + key] = (
                        value if isinstance(value, bool) else str(value)
                    )
                for field, table in spec.references.items():
                    if validated.get(field):
                        reference = await resolver.id(table, validated[field])
                        if not reference or not reference["active"]:
                            raise Problem(409, "UNKNOWN_REFERENCE", "引用已失效")
                        row["references_snapshot"].append(
                            {
                                "table": table,
                                "id": str(reference["id"]),
                                "version": reference["version"],
                            }
                        )
                conversions = []
                if resource == "products" and previous:
                    conversions = [
                        (previous["id"], validated[k])
                        for k in ("default_purchase_unit_id", "default_sales_unit_id")
                    ]
                elif resource == "supplier-products":
                    conversions = [(validated["product_id"], validated["purchase_unit_id"])]
                for product, unit in conversions:
                    conversion = (
                        (
                            await db.execute(
                                text(
                                    "SELECT id,version FROM forge.product_units WHERE "
                                    "organization_id=:org AND product_id=:product AND "
                                    "unit_id=:unit AND active"
                                ),
                                {"org": ctx.organization_id, "product": product, "unit": unit},
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if conversion:
                        row["references_snapshot"].append(
                            {
                                "table": "product_units",
                                "id": str(conversion["id"]),
                                "version": conversion["version"],
                            }
                        )
                # Explicit additional unique keys must be detected at preview as well as at INSERT.
                secondary = []
                if resource == "products" and validated.get("barcode"):
                    secondary.append({"barcode": validated["barcode"]})
                if resource == "supplier-products":
                    secondary.append(
                        {
                            "supplier_id": validated["supplier_id"],
                            "supplier_sku": validated["supplier_sku"],
                        }
                    )
                for key in secondary:
                    where = " AND ".join(f"{field}=:{field}" for field in key)
                    existing = (
                        await db.execute(
                            text(
                                f"SELECT id FROM forge.{spec.table} "
                                f"WHERE organization_id=:org AND {where}"
                            ),
                            {"org": ctx.organization_id, **key},
                        )
                    ).scalar_one_or_none()
                    if existing and existing != row["expected_target_id"]:
                        raise Problem(409, "EXISTING_CODE", "条码或供应商货号已被其他资料使用")
            except ValidationError as exc:
                for item in exc.errors(include_input=False, include_url=False):
                    row["errors"].append(
                        error(
                            ".".join(map(str, item["loc"])),
                            "INVALID_DECIMAL"
                            if item["type"].startswith("decimal")
                            else "REQUIRED"
                            if item["type"] == "missing"
                            else "INVALID_ATTRIBUTE"
                            if resource in {"products", "categories"}
                            else "INVALID_VALUE",
                            validation_message(item),
                        )
                    )
            except (Problem, ValueError) as exc:
                row["errors"].append(
                    error(
                        "",
                        exc.code if isinstance(exc, Problem) else "INVALID_ATTRIBUTE",
                        exc.detail if isinstance(exc, Problem) else str(exc),
                    )
                )
        prepared.append(row)
    return prepared


async def save_preview(db, ctx, resource, mode, sheet):
    prepared = await prepare(db, ctx, resource, mode, sheet)
    preview_hash = fingerprint(
        {
            "headers": sheet.headers,
            "rows": [{key: value for key, value in row.items() if key != "id"} for row in prepared],
        }
    )
    batch_id = uuid4()
    await db.execute(
        text("""INSERT INTO forge.import_batches
        (id,organization_id,resource,mode,filename,file_hash,worksheet,preview_hash,header_order,created_by)
        VALUES(:id,:org,:resource,:mode,:filename,:file_hash,:worksheet,:hash,:headers,:actor)"""),
        {
            "id": batch_id,
            "org": ctx.organization_id,
            "resource": resource,
            "mode": mode,
            "filename": sheet.filename,
            "file_hash": sheet.file_hash,
            "worksheet": sheet.worksheet,
            "hash": preview_hash,
            "headers": sheet.headers,
            "actor": ctx.user_id,
        },
    )
    statement = text(
        "INSERT INTO forge.import_rows(id,organization_id,batch_id,row_no,action,status,"
        "content_hash,raw_values,cleaned_values,command_values,references_snapshot,locator,"
        "expected_target_id,expected_version,errors,error_code) "
        "VALUES(:id,:org,:batch,:row_no,:action,:status,:content_hash,CAST(:raw_values AS jsonb),"
        "CAST(:cleaned_values AS jsonb),CAST(:command_values AS jsonb),"
        "CAST(:references_snapshot AS jsonb),CAST(:locator AS jsonb),"
        ":expected_target_id,:expected_version,CAST(:errors AS jsonb),:error_code)"
    )
    pending = []
    for row in prepared:
        row["status"] = "INVALID" if row["errors"] else "READY"
        row["content_hash"] = fingerprint(
            {key: value for key, value in row.items() if key not in {"id", "status"}}
        )
        pending.append(
            {
                **row,
                **{
                    field: json_dump(row[field])
                    for field in (
                        "raw_values",
                        "cleaned_values",
                        "command_values",
                        "references_snapshot",
                        "locator",
                        "errors",
                    )
                },
                "org": ctx.organization_id,
                "batch": batch_id,
                "error_code": row["errors"][0]["code"] if row["errors"] else None,
            }
        )
        if len(pending) == 250:
            await db.execute(statement, pending)
            pending = []
    if pending:
        await db.execute(statement, pending)
    status = "PREVIEW_INVALID" if any(row["errors"] for row in prepared) else "PREVIEW_READY"
    await record_mutation(
        db,
        ctx,
        "catalog.import.preview",
        "catalog_import",
        batch_id,
        None,
        {"version": 1, "resource": resource, "rows": len(prepared), "status": status},
    )
    return {"id": str(batch_id), "status": status, "version": 1, "request_id": ctx.request_id}
