// 老的"任务详情页"已废弃 — 当前所有 task 都在首页 ?task=tsk_xxx 模式下查看
// 这里只做 server-side redirect,避免旧链接 404 + 不让用户停留在老 UI
import { redirect } from "next/navigation";

export default function TaskRedirect({ params }: { params: { id: string } }) {
  redirect(`/?task=${params.id}`);
}
