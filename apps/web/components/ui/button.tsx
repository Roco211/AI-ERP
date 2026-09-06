"use client";
import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { Button as BeUIButton } from "@/components/motion/button/base";
import { cn } from "@/lib/utils";

type Props = Omit<ButtonPrimitive.Props, "className"> & {
  className?: string;
  variant?: "default" | "primary" | "secondary" | "outline" | "ghost" | "destructive" | "link";
  size?: "default" | "xs" | "sm" | "lg" | "icon" | "icon-xs" | "icon-sm" | "icon-lg";
};

/** Application contract over beUI motion and Base UI interaction semantics. */
export function Button({ variant = "default", size = "default", className, ...props }: Props) {
  const beVariant = variant === "default" ? "primary" : variant === "destructive" ? "secondary" : variant === "link" ? "ghost" : variant;
  const beSize = size.startsWith("icon") ? "icon" : size === "default" ? "md" : size === "xs" ? "sm" : size as "sm" | "lg";
  return <ButtonPrimitive {...props} data-slot="button" render={<BeUIButton variant={beVariant} size={beSize} />} className={cn(
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    variant === "destructive" && "border-destructive/20 bg-destructive/10 text-destructive hover:bg-destructive/20",
    variant === "link" && "underline-offset-4 hover:underline",
    size === "xs" && "h-7 px-2.5 text-xs", className,
  )} />;
}
