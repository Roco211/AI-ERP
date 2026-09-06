import { useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

afterEach(cleanup);

type AmountForm = { amount: string };

function RegisteredInput({ onSubmit }: { onSubmit: (value: AmountForm) => void }) {
  const form = useForm<AmountForm>({ defaultValues: { amount: "15.000000" } });
  // A watched form rerenders when domain-driven setValue/reset changes the value.
  const amount = useWatch({ control: form.control, name: "amount" });
  return <form aria-label="金额表单" onSubmit={form.handleSubmit(onSubmit)}>
    <label htmlFor="amount">金额</label>
    <Input id="amount" inputMode="decimal" {...form.register("amount")} />
    <output aria-label="表单状态">{amount}</output>
    <Button onClick={() => form.setValue("amount", "-20.000001")}>使用服务器金额</Button>
    <Button onClick={() => form.reset({ amount: "3.141592" })}>重置表单</Button>
    <Button onClick={() => form.setValue("amount", "")}>清除旧报价</Button>
    <Button type="submit">提交金额</Button>
  </form>;
}

test("RHF setValue keeps a signed precise server amount after focus and blur rerenders", async () => {
  const submit = vi.fn();
  render(<RegisteredInput onSubmit={submit} />);
  const input = screen.getByRole("textbox", { name: "金额" });
  expect(input).toHaveValue("15.000000");
  fireEvent.click(screen.getByRole("button", { name: "使用服务器金额" }));
  expect(input).toHaveValue("-20.000001");
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input).toHaveValue("-20.000001");
  expect(screen.getByLabelText("表单状态")).toHaveTextContent("-20.000001");
  fireEvent.click(screen.getByRole("button", { name: "提交金额" }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith({ amount: "-20.000001" }, expect.anything()));
});

test("RHF reset and clearing a previous price stay visible and serialize the reset value", async () => {
  const submit = vi.fn();
  render(<RegisteredInput onSubmit={submit} />);
  const input = screen.getByRole("textbox", { name: "金额" });
  fireEvent.change(input, { target: { value: "88.000001" } });
  fireEvent.click(screen.getByRole("button", { name: "清除旧报价" }));
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "重置表单" }));
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input).toHaveValue("3.141592");
  fireEvent.click(screen.getByRole("button", { name: "提交金额" }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith({ amount: "3.141592" }, expect.anything()));
});

test("controlled inputs retain parent state and deliver the actual native change target", () => {
  const observed = vi.fn();
  function Controlled() {
    const [value, setValue] = useState("1.000001");
    return <>
      <Input aria-label="受控金额" name="controlled-amount" value={value} onChange={(event) => {
        observed(event.currentTarget === event.target, event.target.name, event.target.value, event.nativeEvent.type);
        setValue(event.target.value);
      }} />
      <Button onClick={() => setValue("0.000000")}>应用外部状态</Button>
    </>;
  }
  render(<Controlled />);
  const input = screen.getByRole("textbox", { name: "受控金额" });
  fireEvent.change(input, { target: { value: "2.000002" } });
  expect(observed).toHaveBeenCalledWith(true, "controlled-amount", "2.000002", "change");
  expect(input).toHaveValue("2.000002");
  fireEvent.click(screen.getByRole("button", { name: "应用外部状态" }));
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input).toHaveValue("0.000000");
});

test("native uncontrolled inputs preserve ref writes and native form reset", () => {
  function NativeForm() {
    const input = useRef<HTMLInputElement>(null);
    return <form aria-label="原生表单">
      <Input ref={input} name="native-amount" aria-label="原生金额" defaultValue="12.50" />
      <Button onClick={() => { if (input.current) input.current.value = "-4.25"; }}>写入原生值</Button>
    </form>;
  }
  render(<NativeForm />);
  const input = screen.getByRole("textbox", { name: "原生金额" });
  fireEvent.click(screen.getByRole("button", { name: "写入原生值" }));
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input).toHaveValue("-4.25");
  (screen.getByRole("form", { name: "原生表单" }) as HTMLFormElement).reset();
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input).toHaveValue("12.50");
});

test("file input forwards its DOM ref and FileList without controlling a file path", () => {
  const changed = vi.fn();
  const ref = { current: null as HTMLInputElement | null };
  render(<Input ref={ref} aria-label="导入文件" name="import-file" type="file" accept=".xlsx" multiple
    onChange={(event) => changed(event.target, event.target.files)} />);
  const input = screen.getByLabelText("导入文件") as HTMLInputElement;
  const file = new File(["synthetic spreadsheet"], "example.xlsx");
  expect(ref.current).toBe(input);
  expect(input).toHaveAttribute("type", "file");
  expect(input).toHaveAttribute("accept", ".xlsx");
  expect(input).toHaveAttribute("multiple");
  fireEvent.change(input, { target: { files: [file] } });
  expect(changed).toHaveBeenCalledWith(input, [file]);
  fireEvent.focus(input);
  fireEvent.blur(input);
  expect(input.files?.[0]).toBe(file);
  expect(input).toHaveValue("");
});

test("the button adapter preserves submit type, native disabled state and a single submission", () => {
  const submitted = vi.fn();
  const previewed = vi.fn();
  function FormButtons() {
    const [locked, setLocked] = useState(false);
    return <form onSubmit={(event) => { event.preventDefault(); submitted(); setLocked(true); }}>
      <Button variant="outline" onClick={previewed}>读取预览</Button>
      <Button type="submit" disabled={locked}>保存草稿</Button>
    </form>;
  }
  const view = render(<FormButtons />);
  fireEvent.click(screen.getByRole("button", { name: "读取预览" }));
  expect(previewed).toHaveBeenCalledOnce();
  expect(submitted).not.toHaveBeenCalled();
  const save = screen.getByRole("button", { name: "保存草稿" });
  expect(save).toHaveAttribute("type", "submit");
  fireEvent.click(save);
  fireEvent.click(save);
  expect(save).toBeDisabled();
  expect(submitted).toHaveBeenCalledOnce();
  expect(view.container.querySelector("button button")).toBeNull();
});

test.each(["pill", "underline", "segment"] as const)("%s tabs navigate by keyboard, skip disabled choices and unmount old content", (variant) => {
  function Navigation() {
    const [value, setValue] = useState("orders");
    return <Tabs value={value} onValueChange={setValue} variant={variant}>
      <TabsList aria-label="业务分类">
        <TabsTrigger value="orders">订单</TabsTrigger>
        <TabsTrigger value="restricted" disabled>无权限的记录</TabsTrigger>
        <TabsTrigger value="shipments">出库</TabsTrigger>
        <TabsTrigger value="returns">退货</TabsTrigger>
      </TabsList>
      <TabsContent value="orders">订单明细</TabsContent>
      <TabsContent value="shipments">出库明细</TabsContent>
      <TabsContent value="returns">退货明细</TabsContent>
    </Tabs>;
  }
  render(<Navigation />);
  const orders = screen.getByRole("tab", { name: "订单" });
  const shipments = screen.getByRole("tab", { name: "出库" });
  const returns = screen.getByRole("tab", { name: "退货" });
  expect(orders).toHaveAttribute("tabindex", "0");
  expect(shipments).toHaveAttribute("tabindex", "-1");
  fireEvent.keyDown(orders, { key: "ArrowRight" });
  expect(shipments).toHaveFocus();
  expect(shipments).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByText("订单明细")).not.toBeInTheDocument();
  expect(screen.getByRole("tabpanel")).toHaveTextContent("出库明细");
  fireEvent.keyDown(shipments, { key: "End" });
  expect(returns).toHaveFocus();
  fireEvent.keyDown(returns, { key: "ArrowRight" });
  expect(orders).toHaveFocus();
  fireEvent.keyDown(orders, { key: "ArrowLeft" });
  expect(returns).toHaveFocus();
  fireEvent.keyDown(returns, { key: "Home" });
  expect(orders).toHaveFocus();
  fireEvent.click(screen.getByRole("tab", { name: "无权限的记录" }));
  expect(orders).toHaveAttribute("aria-selected", "true");
  expect(screen.getAllByRole("tab").filter((tab) => tab.tabIndex === 0)).toHaveLength(1);
});

test("a locked controlled tab group cannot change the selected business context", () => {
  const changed = vi.fn();
  render(<Tabs value="orders" onValueChange={changed}>
    <TabsList aria-label="锁定记录">
      <TabsTrigger value="orders" disabled>当前订单</TabsTrigger>
      <TabsTrigger value="shipments" disabled>其他出库</TabsTrigger>
    </TabsList>
  </Tabs>);
  fireEvent.click(screen.getByRole("tab", { name: "其他出库" }));
  fireEvent.keyDown(screen.getByRole("tab", { name: "当前订单" }), { key: "ArrowRight" });
  expect(changed).not.toHaveBeenCalled();
  expect(screen.getByRole("tab", { name: "当前订单" })).toHaveAttribute("aria-selected", "true");
});

test.each([false, true])("missing active tab keeps a keyboard entry without automatically changing business state (initial permission %s)", (initialPermission) => {
  const changed = vi.fn();
  function PermissionTabs() {
    const [allowed, setAllowed] = useState(initialPermission);
    const [value, setValue] = useState("prices");
    return <>
      <Button onClick={() => setAllowed(false)}>撤销价格权限</Button>
      <Tabs value={value} onValueChange={(next) => { changed(next); setValue(next); }}>
        <TabsList aria-label="权限变化后的记录">
          <TabsTrigger value="orders">可用订单</TabsTrigger>
          <TabsTrigger value="restricted" disabled>禁用分类</TabsTrigger>
          <TabsTrigger value="shipments">可用出库</TabsTrigger>
          {allowed && <TabsTrigger value="prices">成交价格</TabsTrigger>}
        </TabsList>
        {allowed && <TabsContent value="prices">受限价格明细</TabsContent>}
        <TabsContent value="shipments">可用出库明细</TabsContent>
      </Tabs>
    </>;
  }
  render(<PermissionTabs />);
  if (initialPermission) fireEvent.click(screen.getByRole("button", { name: "撤销价格权限" }));
  expect(screen.queryByText("受限价格明细")).not.toBeInTheDocument();
  const orders = screen.getByRole("tab", { name: "可用订单" });
  expect(orders).toHaveAttribute("tabindex", "0");
  expect(changed).not.toHaveBeenCalled();
  fireEvent.keyDown(orders, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "可用出库" })).toHaveFocus();
  expect(changed).toHaveBeenCalledExactlyOnceWith("shipments");
  expect(screen.getByRole("tabpanel")).toHaveTextContent("可用出库明细");
});

test("restoring a valid active tab removes the fallback entry and never leaves two tab stops", () => {
  const changed = vi.fn();
  function RestoredTabs() {
    const [allowed, setAllowed] = useState(true);
    const [disabled, setDisabled] = useState(false);
    return <>
      <Button onClick={() => setAllowed((old) => !old)}>切换可见权限</Button>
      <Button onClick={() => setDisabled((old) => !old)}>切换锁定状态</Button>
      <Tabs value="prices" onValueChange={changed}>
        <TabsList aria-label="恢复分类">
          <TabsTrigger value="orders">剩余订单</TabsTrigger>
          {allowed && <TabsTrigger value="prices" disabled={disabled}>恢复价格</TabsTrigger>}
        </TabsList>
      </Tabs>
    </>;
  }
  render(<RestoredTabs />);
  const entryNames = () => screen.getAllByRole("tab").filter((tab) => tab.tabIndex === 0 && !tab.hasAttribute("disabled")).map((tab) => tab.textContent);
  expect(entryNames()).toEqual(["恢复价格"]);
  fireEvent.click(screen.getByRole("button", { name: "切换可见权限" }));
  expect(entryNames()).toEqual(["剩余订单"]);
  fireEvent.click(screen.getByRole("button", { name: "切换可见权限" }));
  expect(entryNames()).toEqual(["恢复价格"]);
  fireEvent.click(screen.getByRole("button", { name: "切换锁定状态" }));
  expect(entryNames()).toEqual(["剩余订单"]);
  fireEvent.click(screen.getByRole("button", { name: "切换锁定状态" }));
  expect(entryNames()).toEqual(["恢复价格"]);
  expect(changed).not.toHaveBeenCalled();
});
