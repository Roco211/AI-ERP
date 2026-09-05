import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { Providers } from "@/components/providers";
import LoginPage from "@/app/login/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
test("login presents accessible fields and validates before sending", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  render(<Providers><LoginPage/></Providers>);
  expect(screen.getByLabelText("企业代码")).toBeVisible();
  expect(screen.getByLabelText("密码")).toHaveAttribute("type", "password");
  fireEvent.click(screen.getByRole("button", { name: "进入工作空间" }));
  expect(await screen.findByText("请输入企业代码")).toBeVisible();
  expect(fetchSpy).not.toHaveBeenCalled();
  fetchSpy.mockRestore();
});
