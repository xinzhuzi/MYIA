/**
 * 设置数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动)。
 *
 * 数据面 = sidecar 协议既有方法(entry.py `_HANDLERS`):
 *   secret.set  → 写凭据入系统钥匙链(值零回显零落日志;铁律的唯一写路径);
 *   secret.list → 钥匙链凭据名清单(只有名字,值永不可读);
 *   doctor      → 结构化诊断:credentials(entries 存在性核验)/ enrich 节
 *                 (model 等非凭据字段)/ proxy 池探测 / findings / push_channels。
 *
 * 表单字段 → 持久化路径(与 src/myia/schema.py 契约逐条对齐):
 *   LLM key        → secret.set("myia/llm/api_key")           [钥匙链,本模块直达]
 *   LLM base_url   → 值经 secret.set("myia/llm/base_url") 入钥匙链
 *                    (enrich.base_url 在 YAML 只允许 env:/keychain: 纯引用,
 *                    端点值本体不落盘 —— EnrichConfig._check_endpoint_refs);
 *                    若用户直接填 env:/keychain: 引用则不经界面写,提示入 YAML。
 *   LLM model      → 品类 YAML enrich.model(非凭据);写回属品类 YAML 协议
 *                    缺口(与源管理 sources.write 同一缺口),界面只展示
 *                    doctor 现值 + 校验输入,不伪造保存成功。
 *   代理池凭据     → secret.set("myia/proxy/<pool>");池 URL 结构(pools 节)
 *                    属全局 pools YAML,写回同上属协议缺口;探测走 doctor(config)。
 *   推送通道凭据   → secret.set("myia/<scope>/<name>")(scope=品类 id;
 *                    推送 target 在 YAML 只允许引用,值本体入钥匙链)。
 */
import { api, SidecarRequestError } from "@/lib/api";
import type {
  CredentialEntry,
  DoctorParams,
  DoctorResult,
  EnrichSection,
  Finding,
  ProxyPoolStatus,
  SidecarErrorShape,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 错误归一化(与共享 client.toSidecarError 同规则;独立实现避免动共享层)
// ---------------------------------------------------------------------------

/** 任意抛出物 → SidecarRequestError(组件渲染 code/path/message 用)。 */
export function asSidecarError(raw: unknown): SidecarRequestError {
  if (raw instanceof SidecarRequestError) return raw;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw) as Partial<SidecarErrorShape>;
      if (parsed && typeof parsed.code === "string" && typeof parsed.message === "string") {
        return new SidecarRequestError({
          code: parsed.code,
          path: typeof parsed.path === "string" ? parsed.path : "$",
          message: parsed.message,
          data: parsed.data,
        });
      }
    } catch {
      // 非 JSON 文本:按裸消息包装
    }
    return new SidecarRequestError({ code: "transport_error", path: "$", message: raw });
  }
  return new SidecarRequestError({
    code: "sidecar_unavailable",
    path: "$",
    message: raw instanceof Error ? `Tauri IPC 不可用: ${raw.message}` : `Tauri IPC 不可用: ${String(raw)}`,
  });
}

// ---------------------------------------------------------------------------
// 凭据名规范(secrets.py `_SECRET_NAME_RE` 的 TS 镜像)
// ---------------------------------------------------------------------------

/** 规范名:myia/<scope>/<name>(scope=小写字母/数字/连字符开头;name 含点) */
const SECRET_NAME_RE = /^myia\/([a-z0-9][a-z0-9_-]{0,63})\/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$/;

/** env:/keychain: 引用识别(YAML 只收引用;引用本身不经界面落盘) */
export function isSecretRef(value: string): boolean {
  return /^(env|keychain):/.test(value);
}

/** 凭据名是否规范(不规范的名 sidecar 会结构化拒,这里前置同口径校验) */
export function isCanonicalSecretName(name: string): boolean {
  return SECRET_NAME_RE.test(name);
}

/** LLM 端点/key 的规范名(scope=llm,与品类解耦的全局端点) */
export const SECRET_NAME_LLM_BASE_URL = "myia/llm/base_url";
export const SECRET_NAME_LLM_API_KEY = "myia/llm/api_key";

/** 代理池凭据名:myia/proxy/<pool> */
export function proxySecretName(pool: string): string {
  return `myia/proxy/${pool}`;
}

/** 推送通道凭据名:myia/<scope>/<name>(scope=品类 id,name 默认 token) */
export function pushSecretName(scope: string, name = "token"): string {
  return `myia/${scope}/${name}`;
}

// ---------------------------------------------------------------------------
// 保存:唯一写路径 secret.set(值只过协议,不进组件状态/日志/DOM)
// ---------------------------------------------------------------------------

/** 一次保存动作写入的凭据名(供 doctor 回显对照) */
export interface SecretSaveRecord {
  name: string;
  /** 界面上永不出现值;仅保存「写了哪个名」 */
  savedAt: string;
}

/** 写一个凭据入钥匙链;成功只回名字(值不回程)。 */
export async function saveSecret(name: string, value: string): Promise<SecretSaveRecord> {
  if (!isCanonicalSecretName(name)) {
    throw new TypeError(`凭据名不合规范(须 myia/<scope>/<name>): ${name}`);
  }
  if (!value) {
    throw new TypeError("凭据值为空,不写入");
  }
  await api.secretSet({ name, value });
  return { name, savedAt: new Date().toISOString() };
}

// ---------------------------------------------------------------------------
// doctor 验证回显:保存后的存在性核验 + 现值/发现展示
// ---------------------------------------------------------------------------

export interface EnrichView {
  pluginFile: string;
  enrich: EnrichSection;
}

export interface PushChannelView {
  pluginFile: string;
  channels: string[];
}

export interface DoctorVerify {
  healthy: boolean;
  generatedAt: string;
  /** 凭据存在性核验(界面「已入钥匙链 ✓/✗」的依据;值不可读,只有 exists) */
  credentials: CredentialEntry[];
  /** enrich 节现值(model/batch/budget 等非凭据字段;base_url/api_key 不出协议面) */
  enrichSections: EnrichView[];
  /** 代理池探测结果(doctor 带 --config 时逐池 ok/latency/message) */
  proxyPools: ProxyPoolStatus[];
  proxyConfig: string | null;
  /** 结构化发现(error/warning 全量如实展示) */
  findings: Finding[];
  /** 品类当前声明的推送通道 */
  pushChannels: PushChannelView[];
}

/** doctor 应答 → 设置页回显视图模型(纯函数;错误原样上抛)。 */
export function toDoctorVerify(doctor: DoctorResult): DoctorVerify {
  return {
    healthy: doctor.healthy,
    generatedAt: doctor.generated_at,
    // 回显口径:钥匙链凭据(name = myia/<scope>/<name> 裸名;ref 是带 scheme 的
    // 完整引用,cli.py `_credential_refs`);env 引用不属「已入钥匙链」。
    credentials: doctor.credentials.entries.filter(
      (entry) => entry.kind === "keychain" && entry.name.startsWith("myia/"),
    ),
    enrichSections: doctor.plugins
      .filter((plugin) => plugin.enrich !== null)
      .map((plugin) => ({ pluginFile: plugin.file, enrich: plugin.enrich as EnrichSection })),
    proxyPools: doctor.proxy.pools,
    proxyConfig: doctor.proxy.config,
    findings: doctor.findings,
    pushChannels: doctor.plugins.map((plugin) => ({
      pluginFile: plugin.file,
      channels: plugin.push_channels,
    })),
  };
}

/** 保存后验证:跑一次 doctor 并装配回显(params 可带 config= 代理池 YAML)。 */
export async function verifyWithDoctor(params?: DoctorParams): Promise<DoctorVerify> {
  return toDoctorVerify(await api.doctor(params ?? {}));
}

/** 钥匙链凭据名清单(secret.list;只有名字,值永不可读)。 */
export async function listSecretNames(): Promise<string[]> {
  return (await api.secretList()).names;
}

// ---------------------------------------------------------------------------
// 表单校验(纯函数;与 schema.py 语义同口径,前端先挡一道)
// ---------------------------------------------------------------------------

export interface LlmFormValues {
  baseUrl: string;
  model: string;
  key: string;
}

export type LlmFormError = "base_url_invalid";

/**
 * LLM 表单校验:base_url 允许空 / http(s) URL(保存时入钥匙链)/
 * env:/keychain: 引用(直接入 YAML,不经界面写)。key 可空(空=不重写)。
 * model 不参与保存动作(它属品类 YAML,写回是协议缺口),校验不拦保存,
 * 只作输入提示(现值以 doctor 回显为准)。
 */
export function validateLlmForm(values: LlmFormValues): LlmFormError | null {
  const base = values.baseUrl.trim();
  if (base && !isSecretRef(base) && !/^https?:\/\//.test(base)) return "base_url_invalid";
  return null;
}

export interface PushFormValues {
  scope: string;
  secretName: string;
  value: string;
}

export type PushFormError = "scope_invalid" | "name_invalid" | "value_empty";

/** 推送通道凭据校验:scope=品类 id 规则小写;name 非空;值非空。 */
export function validatePushForm(values: PushFormValues): PushFormError | null {
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(values.scope)) return "scope_invalid";
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(values.secretName)) return "name_invalid";
  if (!values.value) return "value_empty";
  return null;
}

export interface ProxyFormValues {
  pool: string;
  value: string;
}

export type ProxyFormError = "pool_invalid" | "value_empty";

/** 代理池凭据校验:池名与 schema `pool:<名称>` 语法同口径([A-Za-z0-9_-]+)。 */
export function validateProxyForm(values: ProxyFormValues): ProxyFormError | null {
  if (!/^[A-Za-z0-9_-]+$/.test(values.pool)) return "pool_invalid";
  if (!values.value) return "value_empty";
  return null;
}
