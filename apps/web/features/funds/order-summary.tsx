"use client";

import Link from "next/link";
import type { components } from "@/generated/api/schema";
import { amountText } from "./presentation";
import type { Side } from "./client";

type Summary = components["schemas"]["FundsOrderSummary"];
const statusNames = { UNPAID: "未结算", PARTIAL: "部分结算", PAID: "已结清" };

export function OrderFundsSummary({
  value,
  side,
  partyId,
  permissions,
  locked,
}: {
  value?: Summary | null;
  side: Side;
  partyId: string;
  permissions: string[];
  locked: boolean;
}) {
  if (
    !value ||
    !permissions.includes(side === "AR" ? "funds.ar.read" : "funds.ap.read") ||
    !permissions.includes(
      side === "AR" ? "product.price.read" : "product.cost.read",
    )
  )
    return null;
  const incomplete = value.integration_status === "INCOMPLETE";
  return (
    <section
      aria-label="订单资金结算概览"
      className="min-w-0 space-y-2 rounded-lg bg-card p-4 text-sm"
    >
      <h3 className="font-medium">资金结算</h3>
      {value.integration_status === "NOT_ENABLED" ? (
        <p>资金管理尚未启用，本单未计算结算状态。</p>
      ) : (
        <>
          <p>
            {value.settlement_status
              ? statusNames[value.settlement_status]
              : "结算状态待核对"}
          </p>
          {incomplete && (
            <p>
              仍有 {value.unmapped_document_count}{" "}
              张历史单据需要绑定期初。以下仅统计已经纳入的来源，不代表整张订单的最终结算情况。
            </p>
          )}
          <dl className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3">
            {[
              ["期初已结金额", value.historically_settled_amount],
              ["本系统已结算", value.settled_amount],
              ["本系统已退款", value.refunded_amount],
              [
                side === "AR" ? "尚待收款" : "尚待付款",
                value.settlement_amount,
              ],
              [
                side === "AR" ? "尚待退给客户" : "尚待供应商退款",
                value.refund_amount,
              ],
              ["往来净额", value.balance],
            ].map(([label, amount]) => (
              <div key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="break-all tabular-nums">{amountText(amount)}</dd>
              </div>
            ))}
          </dl>
          <p className="text-muted-foreground">
            期初已结金额来自历史余额核对，不计为本系统实际收付款。
          </p>
        </>
      )}
      <Link
        href={`/funds?side=${side}&party=${encodeURIComponent(partyId)}${incomplete && permissions.includes("funds.opening") ? "&tab=legacy" : ""}`}
        className="inline-block text-primary underline"
        aria-disabled={locked}
        tabIndex={locked ? -1 : undefined}
        onClick={(event) => {
          if (locked) event.preventDefault();
        }}
      >
        查看资金往来
      </Link>
    </section>
  );
}
