import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { useFundsSubmission } from "@/features/funds/use-submission";

vi.mock("@/features/sales/navigation", () => ({
  usePendingNavigationGuard: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("two immediate submissions send exactly one original funds operation", async () => {
  const response = deferred<{ id: string }>();
  const operation = vi.fn(() => response.promise);
  const receipt = vi.fn();
  const { result } = renderHook(useFundsSubmission);
  await act(async () => {
    const first = result.current.submit(operation, receipt);
    const duplicate = result.current.submit(operation, receipt);
    expect(operation).toHaveBeenCalledTimes(1);
    expect(result.current.isLocked()).toBe(true);
    await duplicate;
    response.resolve({ id: "original" });
    await first;
  });
  expect(receipt).toHaveBeenCalledExactlyOnceWith({ id: "original" });
  expect(result.current.locked).toBe(false);
});

test("lost funds response and later permission rejection retain the original key and body", async () => {
  const body = {
    amount: "17.0001",
    allocations: [{ id: "A", amount: "17.0001" }],
  };
  type RequestBody = typeof body;
  const request = vi
    .fn<(key: string, body: RequestBody) => Promise<{ id: string }>>()
    .mockRejectedValueOnce(new TypeError("网络连接中断"))
    .mockRejectedValueOnce(new ApiError(403, "没有资金权限"))
    .mockResolvedValueOnce({ id: "only-one-payment" });
  const receipt = vi.fn();
  const { result } = renderHook(useFundsSubmission);
  await act(() => result.current.submit((key) => request(key, body), receipt));
  expect(result.current.locked).toBe(true);
  expect(result.current.error).toContain("提交结果待确认");
  await act(() => result.current.retry());
  expect(result.current.locked).toBe(true);
  expect(result.current.error).toContain("没有资金权限");
  expect(result.current.error).toContain("提交结果待确认");
  await act(() => result.current.retry());
  expect(request).toHaveBeenCalledTimes(3);
  expect(request.mock.calls[1]).toEqual(request.mock.calls[0]);
  expect(request.mock.calls[2]).toEqual(request.mock.calls[0]);
  expect(receipt).toHaveBeenCalledExactlyOnceWith({ id: "only-one-payment" });
  expect(result.current.locked).toBe(false);
});

test("a new operation cannot replace an uncertain funds request", async () => {
  const original = vi.fn().mockRejectedValue(new TypeError("失联"));
  const replacement = vi.fn().mockResolvedValue({ id: "wrong" });
  const { result } = renderHook(useFundsSubmission);
  await act(() => result.current.submit(original, vi.fn()));
  await act(() => result.current.submit(replacement, vi.fn()));
  expect(replacement).not.toHaveBeenCalled();
  expect(original).toHaveBeenCalledTimes(1);
  expect(result.current.locked).toBe(true);
});

test("a first definitive conflict unlocks the form for explicit refresh", async () => {
  const operation = vi
    .fn()
    .mockRejectedValue(new ApiError(409, "可核销余额已变更"));
  const { result } = renderHook(useFundsSubmission);
  await act(() => result.current.submit(operation, vi.fn()));
  expect(result.current.conflict).toBe(true);
  expect(result.current.locked).toBe(false);
  expect(result.current.error).toBe("可核销余额已变更");
  await act(() => result.current.retry());
  expect(operation).toHaveBeenCalledTimes(1);
});

test("a committed funds receipt followed by a refresh failure never resubmits cash", async () => {
  const operation = vi.fn().mockResolvedValue({ id: "committed" });
  const receipt = vi.fn().mockRejectedValue(new TypeError("详情失联"));
  const { result } = renderHook(useFundsSubmission);
  await act(() => result.current.submit(operation, receipt));
  expect(result.current.locked).toBe(false);
  expect(result.current.uncertain).toBe(false);
  expect(result.current.notice).toContain("操作已提交");
  await act(() => result.current.retry());
  expect(operation).toHaveBeenCalledTimes(1);
});
