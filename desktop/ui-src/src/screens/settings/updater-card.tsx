import { ArrowDownToLine, Download, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SidecarRequestError } from "@/lib/api";

import { ErrorBox } from "./error-box";

/**
 * 软件更新卡片(v1.1.1 更新通道的 UI 侧接线,desktop/UPDATER.md 第六节)。
 *
 * Rust 侧 updater/process 插件已注册(main.rs;勿动);这里走官方 JS API:
 * check() → 无更新回显当前版本 / 有更新展示 version+notes →
 * downloadAndInstall()(Rust 侧验签 + passive 安装)→ relaunch()。
 * 失败统一包装成结构化错误走 ErrorBox,如实展示不吞错。
 */
type UpdaterPhase =
  | "idle" // 未检查
  | "checking" // check() 进行中
  | "up-to-date" // check() 返回 null
  | "available" // check() 返回 Update
  | "installing" // downloadAndInstall() 进行中
  | "restarting"; // 安装完成,relaunch() 已发出

/** updater 插件的裸异常(网络/验签/解析)→ 结构化错误(ErrorBox 契约)。 */
function toUpdaterError(raw: unknown, code: string): SidecarRequestError {
  const message = raw instanceof Error ? raw.message : String(raw);
  return new SidecarRequestError({ code, path: "$", message });
}

export function UpdaterCard() {
  const [phase, setPhase] = useState<UpdaterPhase>("idle");
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [update, setUpdate] = useState<Update | null>(null);
  const [error, setError] = useState<SidecarRequestError | null>(null);

  const busy = phase === "checking" || phase === "installing" || phase === "restarting";

  const handleCheck = useCallback(async () => {
    setPhase("checking");
    setError(null);
    try {
      // 当前版本仅用于回显;取不到(异常环境)不拦检查流程
      const [found, version] = await Promise.all([
        check(),
        getVersion().catch(() => null),
      ]);
      setAppVersion(version);
      setUpdate(found);
      setPhase(found ? "available" : "up-to-date");
    } catch (raw) {
      setError(toUpdaterError(raw, "updater_check_failed"));
      setPhase("idle");
    }
  }, []);

  const handleInstall = useCallback(async () => {
    if (!update) return;
    setPhase("installing");
    setError(null);
    try {
      await update.downloadAndInstall();
      setPhase("restarting");
      await relaunch();
    } catch (raw) {
      setError(toUpdaterError(raw, "updater_install_failed"));
      setPhase("available");
    }
  }, [update]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Download className="size-4 text-muted-foreground" />
          软件更新
        </CardTitle>
        <CardDescription>
          官方签名更新通道(GitHub Releases):下载与安装均在 Rust 侧完成验签,装好后自动重启
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error ? (
          <ErrorBox error={error} onRetry={() => void handleCheck()} retrying={busy} />
        ) : null}

        <div className="flex flex-col gap-1 text-sm" data-testid="updater-status">
          {phase === "checking" ? <span className="text-muted-foreground">正在检查更新…</span> : null}
          {phase === "up-to-date" ? (
            <span className="text-ok">已是最新{appVersion ? `(当前 v${appVersion})` : ""}</span>
          ) : null}
          {phase === "available" && update ? (
            <>
              <span>
                发现新版本 <span className="font-medium">v{update.version}</span>
                {update.currentVersion ? (
                  <span className="text-muted-foreground">(当前 v{update.currentVersion})</span>
                ) : null}
              </span>
              {update.body ? (
                <p className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-md border border-border/60 bg-muted/30 px-3 py-2 font-mono text-xs text-muted-foreground">
                  {update.body}
                </p>
              ) : null}
            </>
          ) : null}
          {phase === "installing" ? (
            <span className="text-muted-foreground">下载并安装中(验签 + passive 安装)…</span>
          ) : null}
          {phase === "restarting" ? <span className="text-ok">已安装,正在重启…</span> : null}
        </div>

        <div className="flex items-center gap-2">
          {phase === "available" || phase === "installing" ? (
            <Button size="sm" onClick={() => void handleInstall()} disabled={busy}>
              <ArrowDownToLine className="size-3.5" />
              下载并安装
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void handleCheck()}
              disabled={busy}
              data-testid="updater-check"
            >
              <RefreshCw className={phase === "checking" ? "size-3.5 animate-spin" : "size-3.5"} />
              检查更新
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
