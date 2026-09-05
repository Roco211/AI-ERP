from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from forge_erp.modules.catalog.domain.schemas import Amount, AttributeDefinition, Factor


def validate_attributes(definitions: list[dict], attributes: dict) -> None:
    schema = [AttributeDefinition.model_validate(item) for item in definitions]
    if set(attributes) - {item.key for item in schema}:
        raise ValueError("商品包含分类模板中未定义的属性")
    for item in schema:
        value = attributes.get(item.key)
        if value is None or value == "":
            if item.required:
                raise ValueError(f"请填写属性：{item.label}")
            continue
        if item.kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"属性 {item.label} 必须是是/否")
        elif not isinstance(value, str) or len(value) > 300:
            raise ValueError(f"属性 {item.label} 必须是文本")
        elif item.kind == "enum" and value not in item.options:
            raise ValueError(f"属性 {item.label} 不在允许选项中")
        elif item.kind == "decimal":
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError(f"属性 {item.label} 必须是数值") from exc
            if not number.is_finite() or abs(number) >= Decimal("1e14"):
                raise ValueError(f"属性 {item.label} 数值超出范围")


class ConversionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    product_id: UUID
    unit_id: UUID
    qty: Amount
    unit_to_base_factor: Factor
    base_qty: Amount
    conversion_version: int

    @classmethod
    def capture(
        cls, product_id: UUID, unit_id: UUID, qty: Decimal, factor: Decimal, version: int
    ) -> ConversionSnapshot:
        # Reject unrepresentable quantities instead of silently rounding a document snapshot.
        from decimal import localcontext

        with localcontext() as context:
            context.prec = 50
            base_qty = qty * factor
        return cls(
            product_id=product_id,
            unit_id=unit_id,
            qty=qty,
            unit_to_base_factor=factor,
            base_qty=base_qty,
            conversion_version=version,
        )
