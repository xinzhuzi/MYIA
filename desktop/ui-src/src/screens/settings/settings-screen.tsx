import { Globe, KeyRound, RefreshCw, Save, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { DoctorParams, SidecarRequestError } from "@/lib/api";
import { api } from "@/lib/api";

import {
  asSidecarError,
  deleteSecretByName,
  isSecretRef,
  listSecretNames,
  proxySecretName,
  pushSecretName,
  saveSecret,
  SECRET_NAME_LLM_API_KEY,
  SECRET_NAME_LLM_BASE_URL,
  validateLlmForm,
  validateProxyForm,
  validatePushForm,
  verifyWithDoctor,
} from "./api";
import type { DoctorVerify, SecretSaveRecord } from "./api";
import { DoctorVerifyPanel } from "./doctor-verify";
import { ErrorBox } from "./error-box";
import { FieldInput } from "./field-input";
import { UpdaterCard } from "./updater-card";
import { VisionForm } from "./vision-form";

/** 通道 → 规范凭据名缺省(target 语义:feishu 卡的 chat_id / tg 的 bot token / webhook 地址) */
const PUSH_SECRET_NAME_BY_CHANNEL: Record<string, string> = {
  feishu_card: "chat_id",
  telegram: "token",
  webhook: "url",
};
type PushChannel = keyof typeof PUSH_SECRET_NAME_BY_CHANNEL;
const PUSH_CHANNELS = Object.keys(PUSH_SECRET_NAME_BY_CHANNEL) as PushChannel[];

interface LlmForm {
  baseUrl: string;
  model: string;
  key: string;
}
interface ProxyForm {
  pool: string;
  value: string;
}
interface PushForm {
  channel: PushChannel;
  scope: string;
  secretName: string;
  value: string;
}

/**
 * 设置:LLM(base_url/model/key)/ 代理池 / 推送通道三个表单 + doctor 验证回显。
 *
 * 铁律落地:key 类输入只经 sidecar secret.set 写入系统钥匙链 —— 值不进组件
 * 持久状态(保存即清)、不经任何 DOM/日志回显;「保存成功」的唯一证据是
 * doctor 回显面板里凭据存在性核验(✓ 已在钥匙链)。model / 池 URL 结构 /
 * 通道声明属品类或全局 YAML(非凭据),其写回是 sidecar 协议缺口(与源管理
 * sources.write 同一缺口),界面如实标注,不伪造保存成功。
 */
export function SettingsScreen() {
  const [llm, setLlm] = useState<LlmForm>({ baseUrl: "", model: "", key: "" });
  const [llmErrors, setLlmErrors] = useState<Partial<Record<"baseUrl", string>>>({});
  const [proxy, setProxy] = useState<ProxyForm>({ pool: "", value: "" });
  const [proxyErrors, setProxyErrors] = useState<Partial<Record<"pool" | "value", string>>>({});
  const [probePath, setProbePath] = useState("");
  const [push, setPush] = useState<PushForm>({ channel: "feishu_card", scope: "", secretName: "chat_id", value: "" });
  const [pushErrors, setPushErrors] = useState<Partial<Record<"scope" | "secretName" | "value", string>>>({});

  const [savingCard, setSavingCard] = useState<string | null>(null);
  /** 最近一次保存写入的凭据名(只有名字;值永不回程) */
  const [lastSaved, setLastSaved] = useState<SecretSaveRecord[]>([]);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<SidecarRequestError | null>(null);

  const [verify, setVerify] = useState<DoctorVerify | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<SidecarRequestError | null>(null);
  const [secretNames, setSecretNames] = useState<string[] | null>(null);
  /** C5:已点击删除、待二次确认的凭据名(inline confirm,不弹系统对话框) */
  const [deletingSecret, setDeletingSecret] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<SidecarRequestError | null>(null);

  const refreshSecretNames = useCallback(async () => {
    try {
      setSecretNames(await listSecretNames());
    } catch {
      setSecretNames(null); // 名清单失败不拦主流程(doctor 回显仍在)
    }
  }, []);

  const runDoctor = useCallback(async (params?: DoctorParams) => {
    setVerifying(true);
    setVerifyError(null);
    try {
      setVerify(await verifyWithDoctor(params));
    } catch (error) {
      setVerifyError(asSidecarError(error));
    } finally {
      setVerifying(false);
    }
  }, []);

  /** C5:删除凭据 → 刷新名清单 + doctor 复核(secret_not_found 等结构化上屏)。 */
  const handleDeleteSecret = useCallback(
    async (name: string) => {
      setDeleteError(null);
      try {
        await deleteSecretByName(name);
        setDeletingSecret(null);
        await refreshSecretNames();
        await runDoctor();
      } catch (error) {
        setDeleteError(asSidecarError(error));
      }
    },
    [refreshSecretNames, runDoctor],
  );

  useEffect(() => {
    void runDoctor();
    void refreshSecretNames();
  }, [runDoctor, refreshSecretNames]);

  const handleLlmSave = useCallback(async () => {
    const formError = validateLlmForm(llm);
    if (formError === "base_url_invalid") {
      setLlmErrors({ baseUrl: "base_url 须为 http(s) 地址或 env:/keychain: 引用" });
      return;
    }
    setLlmErrors({});
    setSavingCard("llm");
    setSaveError(null);
    setSaveNote(null);
    try {
      const saved: SecretSaveRecord[] = [];
      let note: string | null = null;
      const base = llm.baseUrl.trim();
      if (base && isSecretRef(base)) {
        // 引用本身只该出现在 YAML/env;界面不代写、也不把它当值入钥匙链
        note = "base_url 是 env:/keychain: 引用:请把该引用直接写入品类 YAML enrich.base_url(品类 YAML 写回属协议缺口,见 openIssues)。";
      } else if (base) {
        saved.push(await saveSecret(SECRET_NAME_LLM_BASE_URL, base));
        note = "base_url 值已入钥匙链;品类 YAML 侧以 keychain:myia/llm/base_url 引用(schema 只收引用,端点值不落盘)。";
      }
      if (llm.key) {
        saved.push(await saveSecret(SECRET_NAME_LLM_API_KEY, llm.key));
      }
      setLastSaved(saved);
      setSaveNote(note);
      setLlm((prev) => ({ ...prev, key: "" })); // key 保存即清:不留存、不回显
      await runDoctor();
      await refreshSecretNames();
    } catch (error) {
      setSaveError(asSidecarError(error));
    } finally {
      setSavingCard(null);
    }
  }, [llm, refreshSecretNames, runDoctor]);

  const handleProxySave = useCallback(async () => {
    const formError = validateProxyForm(proxy);
    if (formError === "pool_invalid") {
      setProxyErrors({ pool: "池名须为字母/数字/连字符/下划线(与 pool:<名称> 语法同口径)" });
      return;
    }
    if (formError === "value_empty") {
      setProxyErrors({ value: "凭据值必填" });
      return;
    }
    setProxyErrors({});
    setSavingCard("proxy");
    setSaveError(null);
    setSaveNote(null);
    try {
      const saved = [await saveSecret(proxySecretName(proxy.pool.trim()), proxy.value)];
      setLastSaved(saved);
      setSaveNote(
        "池凭据已入钥匙链;全局 pools YAML 侧以 keychain:myia/proxy/<pool> 引用(池 URL 结构写回属协议缺口)。",
      );
      setProxy((prev) => ({ ...prev, value: "" }));
      await runDoctor();
      await refreshSecretNames();
    } catch (error) {
      setSaveError(asSidecarError(error));
    } finally {
      setSavingCard(null);
    }
  }, [proxy, refreshSecretNames, runDoctor]);

  const handleProbe = useCallback(async () => {
    await runDoctor(probePath.trim() ? { config: probePath.trim() } : undefined);
  }, [probePath, runDoctor]);

  const handlePushSave = useCallback(async () => {
    const formError = validatePushForm(push);
    if (formError === "scope_invalid") {
      setPushErrors({ scope: "scope 须为品类 id 规则:小写字母/数字开头,可含连字符" });
      return;
    }
    if (formError === "name_invalid") {
      setPushErrors({ secretName: "凭据名段须为字母/数字开头,可含点/连字符/下划线" });
      return;
    }
    if (formError === "value_empty") {
      setPushErrors({ value: "凭据值必填" });
      return;
    }
    setPushErrors({});
    setSavingCard("push");
    setSaveError(null);
    setSaveNote(null);
    try {
      const saved = [await saveSecret(pushSecretName(push.scope.trim(), push.secretName.trim()), push.value)];
      setLastSaved(saved);
      setSaveNote(
        "通道凭据已入钥匙链;品类 YAML push[].target 侧以 keychain:myia/<scope>/<name> 引用(通道声明写回属协议缺口)。",
      );
      setPush((prev) => ({ ...prev, value: "" }));
      await runDoctor();
      await refreshSecretNames();
    } catch (error) {
      setSaveError(asSidecarError(error));
    } finally {
      setSavingCard(null);
    }
  }, [push, refreshSecretNames, runDoctor]);

  /** G5 前半(10-03-feed-ux):push.test 真发一条测试消息(channel 取表单当前
   *  选中;scope 已填则 target 引用 keychain:myia/<scope>/<secretName>,缺省
   *  走通道默认 env 引用链)。结果行内回显:成功 ok 徽标 / 结构化错误。 */
  const [pushTesting, setPushTesting] = useState(false);
  const [pushTestNote, setPushTestNote] = useState<string | null>(null);
  const [pushTestOk, setPushTestOk] = useState<boolean | null>(null);

  const handlePushTest = useCallback(async () => {
    setPushTesting(true);
    setPushTestNote(null);
    setPushTestOk(null);
    // target 引用:表单 scope/凭据名齐全才组;否则让通道走默认 env 链(如实测)
    const scope = push.scope.trim();
    const name = push.secretName.trim() || PUSH_SECRET_NAME_BY_CHANNEL[push.channel] || "";
    const target = scope && name ? `keychain:myia/${scope}/${name}` : undefined;
    try {
      const result = await api.pushTest({ channel: push.channel, ...(target ? { target } : {}) });
      setPushTestOk(true);
      setPushTestNote(
        result.preview
          ? `测试消息已发(stdout 通道预览):${result.preview.slice(0, 200)}`
          : `测试消息已发(${result.channel})——请到对应客户端查收。`,
      );
    } catch (error) {
      const failure = asSidecarError(error);
      setPushTestOk(false);
      setPushTestNote(`发送失败(${failure.code}):${failure.message}`);
    } finally {
      setPushTesting(false);
    }
  }, [push.channel, push.scope, push.secretName]);

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="设置"
        description="LLM key / 代理池凭据 / 推送通道凭据 —— 全部只入系统钥匙链;doctor 验证回显;软件更新检查"
        actions={
          <Button size="sm" variant="outline" onClick={() => void runDoctor()} disabled={verifying}>
            <RefreshCw className={verifying ? "size-3.5 animate-spin" : "size-3.5"} />
            重新验证
          </Button>
        }
      />

      {saveError ? (
        <div className="px-6">
          <ErrorBox error={saveError} />
        </div>
      ) : null}
      {verifyError ? (
        <div className="px-6">
          <ErrorBox error={verifyError} onRetry={() => void runDoctor()} retrying={verifying} />
        </div>
      ) : null}
      {lastSaved.length > 0 ? (
        <div
          role="status"
          className="mx-6 rounded-md border border-ok/30 bg-ok/10 px-4 py-3 text-sm text-ok"
          data-testid="save-status"
        >
          已写入钥匙链:{lastSaved.map((record) => record.name).join("、")}(值不回显)
          {saveNote ? <span className="mt-1 block text-xs text-muted-foreground">{saveNote}</span> : null}
        </div>
      ) : saveNote ? (
        <div
          role="note"
          className="mx-6 rounded-md border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground"
        >
          {saveNote}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 px-6 xl:grid-cols-2">
        {/* LLM */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <KeyRound className="size-4 text-muted-foreground" />
              LLM 精评
            </CardTitle>
            <CardDescription>
              base_url / key 属凭据类,只经 secret.set 入钥匙链;model 属品类 YAML(写回待协议扩展)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <FieldInput
              label="base_url"
              aria-label="base_url"
              placeholder="https://open.bigmodel.cn/api/paas/v4 或 env:MYIA_LLM_BASE_URL"
              value={llm.baseUrl}
              onChange={(event) => setLlm((prev) => ({ ...prev, baseUrl: event.target.value }))}
              error={llmErrors.baseUrl}
              hint="http(s) 地址 → 值入钥匙链(myia/llm/base_url);env:/keychain: 引用 → 直接写 YAML,不经界面"
            />
            <FieldInput
              label="model"
              aria-label="model"
              placeholder="glm-4-flash(doctor 回显为现值)"
              value={llm.model}
              onChange={(event) => setLlm((prev) => ({ ...prev, model: event.target.value }))}
              hint="非凭据:存于品类 YAML enrich.model,现值见 doctor 回显;不经本表单持久化(写回属协议缺口)"
            />
            <FieldInput
              label="LLM API Key"
              aria-label="LLM API Key"
              type="password"
              autoComplete="new-password"
              placeholder="输入后才写入;保存即清,永不回显"
              value={llm.key}
              onChange={(event) => setLlm((prev) => ({ ...prev, key: event.target.value }))}
              hint="写入 myia/llm/api_key;YAML 侧引用 keychain:myia/llm/api_key"
            />
            <div>
              <Button size="sm" onClick={() => void handleLlmSave()} disabled={savingCard === "llm"}>
                <Save className="size-3.5" />
                保存 LLM 凭据
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* 代理池 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Globe className="size-4 text-muted-foreground" />
              代理池
            </CardTitle>
            <CardDescription>池凭据入钥匙链;池 URL 结构在全局 pools YAML(探测走 doctor --config)</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-2">
              <FieldInput
                label="池名"
                aria-label="代理池名"
                placeholder="main"
                value={proxy.pool}
                onChange={(event) => setProxy((prev) => ({ ...prev, pool: event.target.value }))}
                error={proxyErrors.pool}
              />
              <FieldInput
                label="凭据值"
                aria-label="代理凭据值"
                type="password"
                autoComplete="new-password"
                placeholder="写入 myia/proxy/<池名>;永不回显"
                value={proxy.value}
                onChange={(event) => setProxy((prev) => ({ ...prev, value: event.target.value }))}
                error={proxyErrors.value}
              />
            </div>
            <div className="flex items-end gap-2">
              <Button size="sm" variant="outline" onClick={() => void handleProxySave()} disabled={savingCard === "proxy"}>
                <Save className="size-3.5" />
                保存代理凭据
              </Button>
            </div>
            <div className="flex items-end gap-2 border-t border-border/60 pt-3">
              <div className="min-w-0 flex-1">
                <FieldInput
                  label="全局 pools YAML 路径"
                  aria-label="pools YAML 路径"
                  placeholder="config/pools.yaml(--config;缺省=只看现状)"
                  value={probePath}
                  onChange={(event) => setProbePath(event.target.value)}
                />
              </div>
              <Button size="sm" variant="secondary" onClick={() => void handleProbe()} disabled={verifying}>
                探测
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* 推送通道 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Send className="size-4 text-muted-foreground" />
              推送通道
            </CardTitle>
            <CardDescription>
              通道凭据(chat_id / bot token / webhook)入钥匙链;「发送测试」真发一条验证通道连通(push.test);
              通道启停与阈值路由在品类 YAML push: 节
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-xs text-muted-foreground">通道</span>
                <Select
                  value={push.channel}
                  onValueChange={(channel) =>
                    setPush((prev) => ({
                      ...prev,
                      channel: channel as PushChannel,
                      secretName: PUSH_SECRET_NAME_BY_CHANNEL[channel] ?? prev.secretName,
                    }))
                  }
                >
                  <SelectTrigger aria-label="推送通道" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PUSH_CHANNELS.map((channel) => (
                      <SelectItem key={channel} value={channel}>
                        {channel}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <FieldInput
                label="品类 scope"
                aria-label="品类 scope"
                placeholder="stocks"
                value={push.scope}
                onChange={(event) => setPush((prev) => ({ ...prev, scope: event.target.value }))}
                error={pushErrors.scope}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <FieldInput
                label="凭据名段"
                aria-label="推送凭据名"
                placeholder={PUSH_SECRET_NAME_BY_CHANNEL[push.channel]}
                value={push.secretName}
                onChange={(event) => setPush((prev) => ({ ...prev, secretName: event.target.value }))}
                error={pushErrors.secretName}
              />
              <FieldInput
                label="凭据值"
                aria-label="推送凭据值"
                type="password"
                autoComplete="new-password"
                placeholder="写入 myia/<scope>/<name>;永不回显"
                value={push.value}
                onChange={(event) => setPush((prev) => ({ ...prev, value: event.target.value }))}
                error={pushErrors.value}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => void handlePushSave()} disabled={savingCard === "push"}>
                <Save className="size-3.5" />
                保存推送凭据
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void handlePushTest()}
                disabled={pushTesting}
                title="真发一条测试消息(push.test):验证所选通道连通性"
              >
                <Send className={pushTesting ? "size-3.5 animate-pulse" : "size-3.5"} />
                {pushTesting ? "发送中…" : "发送测试"}
              </Button>
              {pushTestOk === true ? <Badge variant="ok">通道连通</Badge> : null}
              {pushTestOk === false ? <Badge variant="destructive">通道失败</Badge> : null}
            </div>
            {pushTestNote ? (
              <p
                role={pushTestOk === false ? "alert" : "status"}
                data-testid="push-test-result"
                className={pushTestOk === false ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
              >
                {pushTestNote}
              </p>
            ) : null}
          </CardContent>
        </Card>

        {/* 看图配置(10-03-vision-pipeline 拆屏后看图在桌面的唯一保留面:
            通道/引擎结构配置 + 云端 key 入钥匙链;采集图析在 feed 屏呈现) */}
        <VisionForm secretNames={secretNames} />

        {/* 钥匙链名清单(+ 删除入口,C5) */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">钥匙链凭据名(secret.list)</CardTitle>
            <CardDescription>只有名字,值永不可读(secrets.py 契约);删除需二次确认</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {deleteError ? <ErrorBox error={deleteError} /> : null}
            {secretNames === null ? (
              <span className="text-xs text-muted-foreground">无法获取(secret.list 失败或环境不可用)</span>
            ) : secretNames.length === 0 ? (
              <span className="text-xs text-muted-foreground">暂无凭据</span>
            ) : (
              <div className="flex flex-wrap items-center gap-1.5">
                {secretNames.map((name) => (
                  <span key={name} className="flex items-center gap-0.5">
                    <Badge variant="outline" className="font-mono">
                      {name}
                    </Badge>
                    {deletingSecret === name ? (
                      <>
                        <Button
                          size="sm"
                          variant="destructive"
                          data-testid={`confirm-delete-${name}`}
                          onClick={() => void handleDeleteSecret(name)}
                        >
                          确认删除
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setDeletingSecret(null)}>
                          取消
                        </Button>
                      </>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6"
                        aria-label={`删除凭据 ${name}`}
                        title={`删除 ${name}:删除后引用该凭据的源将采集失败`}
                        onClick={() => {
                          setDeleteError(null);
                          setDeletingSecret(name);
                        }}
                      >
                        <Trash2 className="size-3" />
                      </Button>
                    )}
                  </span>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 软件更新(官方签名更新通道,updater-card.tsx) */}
        <UpdaterCard />
      </div>

      <div className="px-6">
        <Card>
          <CardHeader>
            <CardTitle>保存后验证(doctor 回显)</CardTitle>
            <CardDescription>
              一切回显来自 doctor 应答:凭据存在性核验 + enrich 现值 + 池探测 + 结构化发现
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DoctorVerifyPanel verify={verify} loading={verifying} />
          </CardContent>
        </Card>
      </div>

      <p className="px-6 text-[11px] text-muted-foreground">
        安全底线:任何凭据输入只经协议 secret.set 写入系统钥匙链(macOS Keychain /
        Windows DPAPI);配置文件出现明文凭据 = 启动即报错拒跑。model / 池 URL 结构 /
        通道声明的品类 YAML 写回属 sidecar 协议缺口,已记 openIssues,界面不伪造保存成功。
      </p>
    </div>
  );
}
