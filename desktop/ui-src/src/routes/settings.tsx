import { Bot, GitBranch, KeyRound, Send, ToggleLeft } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** 设置分区(信息架构照 PRD 五界面之「设置」;表单与写回 C 阶段) */
const SECTIONS = [
  {
    icon: KeyRound,
    title: "LLM Key",
    description: "凭据只入系统钥匙链(secret.set),界面零回显、YAML 只写 keychain: 引用",
  },
  {
    icon: GitBranch,
    title: "代理池",
    description: "全局 pools YAML 的查看与探测(doctor 已可结构化诊断)",
  },
  {
    icon: Send,
    title: "推送通道",
    description: "feishu_card / telegram / webhook 的启停与阈值分级路由",
  },
  {
    icon: Bot,
    title: "反馈开关",
    description: "LLM 精评与反馈回路的预算护栏开关",
  },
] as const;

/**
 * 设置(骨架):四个分区先立信息架构。
 * 表单与写回(secret.set / 品类 YAML)属业务逻辑,C 阶段接入。
 */
export function SettingsPage() {
  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="设置"
        description="LLM key(入钥匙链)/ 代理池 / 推送通道 / 反馈开关"
      />

      <div className="grid grid-cols-1 gap-3 px-6 md:grid-cols-2">
        {SECTIONS.map(({ icon: Icon, title, description }) => (
          <Card key={title}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Icon className="size-4 text-muted-foreground" />
                {title}
              </CardTitle>
              <CardDescription>{description}</CardDescription>
            </CardHeader>
            <CardContent className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <ToggleLeft className="size-3.5" />
                待配置
              </span>
              <Badge variant="outline">C 阶段接入</Badge>
            </CardContent>
          </Card>
        ))}
      </div>

      <p className="px-6 text-[11px] text-muted-foreground">
        安全底线:任何凭据输入只经协议 secret.set 写入系统钥匙链(macOS Keychain /
        Windows DPAPI);配置文件出现明文凭据 = 启动即报错拒跑。
      </p>
    </div>
  );
}
