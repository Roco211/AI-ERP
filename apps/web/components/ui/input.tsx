"use client";
import { forwardRef, type ComponentProps, type ReactNode } from "react";
import { Input as BeUIInput } from "@/components/motion/input";
import { cn } from "@/lib/utils";

export const Input = forwardRef<HTMLInputElement, ComponentProps<"input"> & { leftIcon?: ReactNode; rightIcon?: ReactNode }>(function Input(
  { className, value, defaultValue, onChange, ...props }, ref,
) {
  return <BeUIInput {...props} nativeForm ref={ref} value={value === undefined ? undefined : String(value)}
    defaultValue={defaultValue === undefined ? undefined : String(defaultValue)} onNativeChange={onChange}
    error={props["aria-invalid"] === true || props["aria-invalid"] === "true"}
    data-slot="input" className={cn("w-full min-w-0", className)}
    classNames={{ field: "bg-background", input: "text-sm" }} />;
});
