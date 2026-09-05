"use client";
import { useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ArrowRight, Hexagon, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
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
  return <main className="grid min-h-screen lg:grid-cols-[1.08fr_1fr]">
    <section className="relative flex flex-col justify-between overflow-hidden bg-[#203e30] px-10 py-9 text-white lg:px-16 lg:py-12">
      <div className="flex items-center gap-3 text-xl font-semibold tracking-tight"><Hexagon size={30} /> FORGE <span className="ml-1 text-xs font-normal tracking-[.18em] text-white/45">ERP</span></div>
      <div className="py-16 lg:py-24"><p className="mb-6 text-xs tracking-[.25em] text-[#a0bda9]">为每一天的经营，打好基础</p>
        <h1 className="max-w-lg text-4xl leading-snug font-medium lg:text-5xl">生意有条理。<br /><span className="text-[#adcaad]">经营更从容。</span></h1>
        <p className="mt-7 max-w-sm text-sm leading-7 text-white/60">从一个清晰、有序的工作台开始，<br />让团队在同一个空间协作。</p>
        <div aria-hidden className="mt-12 flex items-end gap-3 opacity-40"><div className="h-12 w-16 rounded-t bg-[#88ac95]"/><div className="h-20 w-16 rounded-t bg-[#88ac95]"/><div className="h-28 w-16 rounded-t bg-[#c2d7c0]"/><div className="h-36 w-16 rounded-t bg-[#eef2dc]"/></div>
      </div>
      <p className="text-xs text-white/40">FORGE ERP · 企业工作空间</p>
    </section>
    <section className="flex items-center justify-center px-7 py-14">
      <div className="w-full max-w-sm"><p className="mb-3 text-xs font-medium tracking-widest text-muted-foreground">欢迎回来</p><h2 className="text-3xl font-semibold tracking-tight">登录工作空间</h2><p className="mt-3 text-sm text-muted-foreground">使用企业分配的账户继续。</p>
        <form onSubmit={submit} className="mt-9 space-y-5">
          <div className="space-y-2"><Label htmlFor="organization">企业代码</Label><Input id="organization" placeholder="例如 DEMO" autoComplete="organization" {...register("organization_code")} aria-invalid={!!errors.organization_code}/>{errors.organization_code && <p className="text-xs text-destructive">{errors.organization_code.message}</p>}</div>
          <div className="space-y-2"><Label htmlFor="email">邮箱</Label><Input id="email" type="email" placeholder="name@company.com" autoComplete="username" {...register("email")} aria-invalid={!!errors.email}/>{errors.email && <p className="text-xs text-destructive">{errors.email.message}</p>}</div>
          <div className="space-y-2"><Label htmlFor="password">密码</Label><Input id="password" type="password" autoComplete="current-password" placeholder="输入密码" {...register("password")} aria-invalid={!!errors.password}/>{errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}</div>
          {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={isSubmitting} className="h-11 w-full">{isSubmitting ? "正在登录…" : "进入工作空间"}<ArrowRight className="ml-2 size-4" /></Button>
        </form>
        <div className="mt-8 flex gap-2 border-t border-border pt-5 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0"/><p>账户仅用于所属企业。需要开通或找回账户，请联系企业管理员。</p></div>
      </div>
    </section>
  </main>;
}
