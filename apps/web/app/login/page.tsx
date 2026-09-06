"use client";
import { useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { ThemeToggle } from "@/components/motion/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const formSchema = z.object({ organization_code: z.string().min(1, "请输入企业代码"),
  email: z.email("请输入有效邮箱"), password: z.string().min(1, "请输入密码") });

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const attempt = useRef<{ signature: string; key: string } | null>(null);
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm({
    resolver: zodResolver(formSchema), defaultValues: { organization_code: "", email: "", password: "" }
  });
  async function submit(event: FormEvent<HTMLFormElement>) {
    await handleSubmit(async (body) => {
    setError("");
    const signature = JSON.stringify(body);
    if (attempt.current?.signature !== signature) attempt.current = { signature, key: crypto.randomUUID() };
    try {
      const result = await api.POST("/api/v1/auth/login", { body,
        params: { header: { "idempotency-key": attempt.current.key } } });
      if (result.error) {
        attempt.current = null;
        setError(result.response.status === 401 ? "企业代码、邮箱或密码不正确。" :
          `暂时无法登录，请重试。参考编号：${result.error.request_id}`);
        return;
      }
      attempt.current = null;
      queryClient.clear();
      router.replace("/dashboard");
    } catch { setError("网络连接中断，请重试本次登录。"); }
    })(event);
  }
  return <main className="flex min-h-svh flex-col bg-background px-5 py-7 sm:px-10">
    <header className="flex items-center gap-2.5 text-sm font-medium"><span className="grid size-7 place-items-center rounded-lg bg-foreground text-background">F</span> Forge ERP<ThemeToggle aria-label="切换明暗主题" variant="circle" className="ml-auto size-9 rounded-full hover:bg-muted" iconClassName="size-4" /></header>
    <section className="flex flex-1 items-center justify-center py-16">
      <div className="w-full max-w-[380px]">
        <div className="mb-8"><span className="mb-5 inline-flex rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground">企业工作空间</span><h1 className="text-[28px] font-medium tracking-tight">登录工作空间</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">欢迎回来。使用企业分配的账户继续。</p></div>
        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-2"><Label htmlFor="organization">企业代码</Label><Input id="organization" placeholder="例如 DEMO" autoComplete="organization" {...register("organization_code")} aria-invalid={!!errors.organization_code} aria-describedby={errors.organization_code ? "organization-error" : undefined}/>{errors.organization_code && <p id="organization-error" className="text-xs text-destructive">{errors.organization_code.message}</p>}</div>
          <div className="space-y-2"><Label htmlFor="email">邮箱</Label><Input id="email" type="email" placeholder="name@company.com" autoComplete="username" {...register("email")} aria-invalid={!!errors.email} aria-describedby={errors.email ? "email-error" : undefined}/>{errors.email && <p id="email-error" className="text-xs text-destructive">{errors.email.message}</p>}</div>
          <div className="space-y-2"><Label htmlFor="password">密码</Label><Input id="password" type="password" autoComplete="current-password" placeholder="输入密码" {...register("password")} aria-invalid={!!errors.password} aria-describedby={errors.password ? "password-error" : undefined}/>{errors.password && <p id="password-error" className="text-xs text-destructive">{errors.password.message}</p>}</div>
          {error && <p role="alert" className="rounded-xl border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={isSubmitting} className="h-11 w-full">{isSubmitting ? "正在登录…" : "进入工作空间"}<ArrowRight className="ml-2 size-4" /></Button>
        </form>
        <div className="mt-8 flex gap-2 border-t border-border pt-5 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0"/><p>账户仅用于所属企业。需要开通或找回账户，请联系企业管理员。</p></div>
      </div>
    </section>
    <footer className="text-center text-xs text-muted-foreground/70">Forge ERP · 让经营更从容</footer>
  </main>;
}
