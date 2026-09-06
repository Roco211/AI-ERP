"use client";

import { useId, useState } from "react";
import { ChevronLeft, ChevronRight, LockKeyhole, MessageSquare, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ConversationSummary } from "./assistant-client";

export interface ConversationHistoryProps {
  items: ConversationSummary[];
  selectedId?: string;
  page: number;
  total: number;
  loading: boolean;
  error: boolean;
  locked: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onPage: (page: number) => void;
}

const shortTime = new Intl.DateTimeFormat("zh-CN", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
});
const fullTime = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

export function ConversationHistory({
  items, selectedId, page, total, loading, error, locked, onSelect, onNew, onPage,
}: ConversationHistoryProps) {
  const searchHintId = useId();
  const [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase("zh-CN");
  const matches = items.filter((item) => item.title.toLocaleLowerCase("zh-CN").includes(query));
  const changePage = (next: number) => {
    if (locked || loading) return;
    setSearch("");
    onPage(next);
  };

  return <aside aria-label="对话历史" className="flex h-full min-h-0 min-w-0 flex-col gap-4 bg-background/60 p-3">
    <header className="flex items-center justify-between gap-2">
      <h2 className="text-sm font-medium">我的对话</h2>
      <Button size="xs" variant="ghost" disabled={locked} onClick={onNew}>
        <Plus aria-hidden className="size-3.5" />新对话
      </Button>
    </header>

    <div className="space-y-2">
      <Input type="search" aria-label="搜索本页对话" aria-describedby={searchHintId}
        placeholder="搜索本页对话" autoComplete="off" maxLength={200} value={search}
        disabled={loading || error || !items.length} leftIcon={<Search aria-hidden />}
        onChange={(event) => setSearch(event.target.value)} />
      <p id={searchHintId} className="px-1 text-xs leading-5 text-muted-foreground">仅搜索当前页对话</p>
    </div>

    <nav aria-label="对话列表" aria-busy={loading}
      className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overscroll-contain">
      {loading ? <p role="status" className="px-2 py-6 text-sm text-muted-foreground">正在读取对话…</p>
        : error ? <p role="alert" className="px-2 py-6 text-sm text-destructive">暂时无法读取对话列表。</p>
          : !items.length ? <div className="px-2 py-6 text-center">
            <MessageSquare aria-hidden className="mx-auto mb-3 size-5 text-muted-foreground" />
            <p role="status" className="text-sm text-muted-foreground">{total ? "本页暂无对话。" : "还没有对话。"}</p>
            {!total && <p className="mt-2 text-xs leading-5 text-muted-foreground">点击「新对话」开始。</p>}
          </div>
            : !matches.length ? <p role="status" className="px-2 py-6 text-sm leading-6 text-muted-foreground">本页没有匹配的对话，可更换关键词。</p>
              : matches.map((item) => {
                const createdAt = new Date(item.created_at);
                const validTime = !Number.isNaN(createdAt.getTime());
                return <Button key={item.id} variant={item.id === selectedId ? "secondary" : "ghost"}
                  aria-current={item.id === selectedId ? "page" : undefined} aria-label={item.title}
                  title={item.title} disabled={locked} onClick={() => onSelect(item.id)}
                  className="h-auto min-h-16 w-full min-w-0 flex-col items-start gap-1.5 rounded-xl px-3 py-3 text-left">
                  <span className="flex w-full min-w-0 items-center gap-2">
                    <MessageSquare aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 truncate text-xs font-medium">{item.title}</span>
                  </span>
                  <time dateTime={item.created_at} title={validTime ? fullTime.format(createdAt) : item.created_at}
                    className="pl-5.5 text-[11px] font-normal text-muted-foreground">
                    {validTime ? shortTime.format(createdAt) : "时间暂不可用"}
                  </time>
                </Button>;
              })}
    </nav>

    <footer className="space-y-3 border-t border-border/60 pt-3">
      {(total > 20 || page > 1) && <div className="flex items-center justify-between gap-1" aria-label="对话分页">
        <Button variant="ghost" size="icon" aria-label="上一页" disabled={locked || loading || page <= 1}
          onClick={() => changePage(page - 1)}><ChevronLeft aria-hidden /></Button>
        <span className="text-xs tabular-nums text-muted-foreground">第 {page} 页</span>
        <Button variant="ghost" size="icon" aria-label="下一页" disabled={locked || loading || page * 20 >= total}
          onClick={() => changePage(page + 1)}><ChevronRight aria-hidden /></Button>
      </div>}
      <p className="flex items-start gap-2 px-1 text-xs leading-5 text-muted-foreground">
        <LockKeyhole aria-hidden className="mt-0.5 size-3.5 shrink-0" />
        <span>仅自己可见<br />对话正文保留 7 天</span>
      </p>
    </footer>
  </aside>;
}
