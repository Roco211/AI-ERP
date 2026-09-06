import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useForm, useWatch } from "react-hook-form";
import Link from "next/link";
import { afterEach, expect, test, vi } from "vitest";
import { Input } from "@/components/ui/input";
import { BusinessTable } from "@/features/operations/business-table";

afterEach(cleanup);

test("beUI native input retains exact server values through RHF updates, focus and reset", async () => {
  const submitted = vi.fn();
  function ServerValueForm() {
    const form = useForm({ defaultValues: { amount: "0.000000" } });
    const amount = useWatch({ control: form.control, name: "amount" });
    return <form onSubmit={form.handleSubmit(submitted)}>
      <Input aria-label="服务端金额" {...form.register("amount")} />
      <output aria-label="已载入金额">{amount}</output>
      <button type="button" onClick={() => form.setValue("amount", "15.000000")}>载入服务端单价</button>
      <button type="button" onClick={() => form.setValue("amount", "-20.000000")}>载入负数历史余额</button>
      <button type="button" onClick={() => form.reset({ amount: "0.000000" })}>重置草稿</button>
      <button type="submit">保存原始金额</button>
    </form>;
  }
  render(<ServerValueForm />);
  const input = screen.getByRole("textbox", { name: "服务端金额" });
  fireEvent.click(screen.getByRole("button", { name: "载入服务端单价" }));
  fireEvent.focus(input);
  expect(input).toHaveValue("15.000000");
  fireEvent.click(screen.getByRole("button", { name: "载入负数历史余额" }));
  fireEvent.blur(input);
  expect(input).toHaveValue("-20.000000");
  fireEvent.click(screen.getByRole("button", { name: "保存原始金额" }));
  await waitFor(() => expect(submitted).toHaveBeenCalledWith({ amount: "-20.000000" }, expect.anything()));
  fireEvent.click(screen.getByRole("button", { name: "重置草稿" }));
  fireEvent.focus(input);
  expect(input).toHaveValue("0.000000");
});

test("beUI business table preserves the complete server page, order and exact source values", () => {
  const rows = Array.from({ length: 25 }, (_, index) => ({
    id: `source-${25 - index}`,
    cells: [
      `来源 ${25 - index}`,
      "12345678901234567890.000001",
      <Link key="source" href={`/sales?document=source-${25 - index}`}>查看来源 {25 - index}</Link>,
    ],
  }));
  render(<BusinessTable headers={["业务来源", "金额", "操作"]} rows={rows} />);
  const renderedRows = screen.getAllByRole("row");
  expect(renderedRows).toHaveLength(26);
  expect(within(renderedRows[1]).getByText("来源 25")).toBeVisible();
  expect(within(renderedRows[25]).getByText("来源 1")).toBeVisible();
  expect(screen.getAllByText("12345678901234567890.000001")).toHaveLength(25);
  expect(screen.getByRole("link", { name: /^查看来源 1$/ })).toHaveAttribute("href", "/sales?document=source-1");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

test("beUI business table keeps rich source details and the supplied empty state", () => {
  const { rerender } = render(<BusinessTable headers={["来源"]} rows={[{
    id: "source",
    cells: [<details key="detail" open><summary>成交依据</summary><p>原单位换算率：24.000000</p><Link href="/sales?tab=prices">查看成交记录</Link></details>],
  }]} />);
  expect(screen.getByText("原单位换算率：24.000000")).toBeVisible();
  expect(screen.getByRole("link", { name: "查看成交记录" })).toBeVisible();
  rerender(<BusinessTable headers={["来源"]} rows={[]} empty="当前没有已授权的来源。" />);
  expect(screen.getByText("当前没有已授权的来源。")).toBeVisible();
  expect(screen.queryByRole("link", { name: "查看成交记录" })).not.toBeInTheDocument();
});
