/**
 * MYIA 桌面 sidecar 协议类型(权威定义:desktop/entry.py 模块注释)。
 *
 * 线协议 = 行分隔 JSON-RPC 子集:
 *   请求  {"id", "method", "params"} → 应答 {"id", "result"} | {"id", "error"}
 *   错误  {"code", "path", "message", "data?"}(code/path/message 必有)
 *   事件  无 id、以 "type" 字段区分:log / progress / completed,
 *         由 Tauri 壳原样转发为事件 `sidecar://event`。
 *
 * 前端唯一入口:壳命令 invoke("sidecar_request", { method, params });
 * 壳侧 Err(String) 即结构化错误对象的 JSON 文本(见 src-tauri/src/main.rs)。
 *
 * 数据形状来源(逐一对照,勿凭记忆臆造):
 *   version / health / plugins.list / doctor / run 系列 / logs.tail / secret 系列
 *   → desktop/entry.py `_HANDLERS` 各方法函数;
 *   health  → src/myia/cli.py `_cmd_list` + `_m_health` 追加的 summary/healthy/exit_code;
 *   doctor  → cli.py `_doctor_payload`(identity/sources/enrich/credentials/proxy/findings);
 *   plugins.list → cli.py `_plugin_list` + src/myia/plugins/installed.py `to_dict`;
 *   store.items  → entry.py `_item_dict`(raw/content_hash 不出协议面);
 *   源健康度     → cli.py `evaluate_source_health` / `_fingerprint_skip_stats`。
 */

// ---------------------------------------------------------------------------
// 通用:结构化错误(协议级 + 业务级,业务级 code 透传 CLI)
// ---------------------------------------------------------------------------

export interface SidecarErrorShape {
  /** 错误类:协议级 parse_error/invalid_request/invalid_params/method_not_found/internal_error;
   *  壳级 sidecar_not_running/sidecar_dropped/sidecar_timeout/sidecar_terminated;
   *  业务级 config/plugins_dir/store_corrupt/invalid_secret_name/run_busy/run_not_found/… */
  code: string;
  /** 字段路径(如 "$"、"params.since") */
  path: string;
  /** 中文原因 */
  message: string;
  /** 原始细节(CLI 报文整包等) */
  data?: unknown;
}

// ---------------------------------------------------------------------------
// version
// ---------------------------------------------------------------------------

export interface VersionParams {}

export interface VersionResult {
  name: string;
  /** myia.__version__ */
  version: string;
  /** 协议版本(PROTOCOL_VERSION,当前 1) */
  protocol: number;
}

// ---------------------------------------------------------------------------
// health(myia list --json 等价 + summary/healthy/exit_code 增强)
// ---------------------------------------------------------------------------

export interface HealthParams {
  /** 品类 YAML 目录(缺省 = 已装插件目录默认值) */
  plugins_dir?: string;
  /** SQLite 库路径(缺省 = 默认库) */
  db?: string;
}

export type SourceHealthState = "ok" | "degraded" | "dead" | "unknown";

export interface LatestObservation {
  run_id: number | string;
  run_status: string;
  item_count: number;
  skip_reason: string | null;
  failed: boolean;
}

export interface SourceHealth {
  state: SourceHealthState;
  reason: string;
  observed: number;
  latest: LatestObservation | null;
  /** 被评判轮之前近 5 次有产出均值;样本不足 5 次为 null */
  baseline: number | null;
}

export interface FingerprintSkips {
  observed: number;
  skipped: number;
}

export interface SourceReport {
  name: string;
  url: string;
  engine: string;
  engine_hint: string | null;
  health: SourceHealth;
  fingerprint_skips: FingerprintSkips;
}

export interface LoadErrorDetail {
  error_type: string;
  path: string;
  message: string;
}

export interface PluginReport {
  file: string;
  id: string | null;
  name: string | null;
  schedule: string | null;
  timezone: string | null;
  push_channels: string[];
  loaded: boolean;
  /** 加载失败明细;成功为 null */
  load_errors: LoadErrorDetail[] | null;
  sources: SourceReport[];
}

export interface HealthSummary {
  plugins: number;
  sources: number;
  ok: number;
  degraded: number;
  dead: number;
  unknown: number;
}

export interface StoreErrorDetail {
  error_type: string;
  message: string;
  [key: string]: unknown;
}

export interface HealthResult {
  command: "list";
  plugins_dir: string;
  db: string;
  store_error: StoreErrorDetail | null;
  plugins: PluginReport[];
  summary: HealthSummary;
  /** healthy 语义对齐 doctor:dead=error 级;degraded 只算 warning */
  healthy: boolean;
  /** v1.1.1:数据根内零品类 YAML(真·首跑/种子失败);UI 据此给初始化引导而非报错 */
  first_run?: boolean;
  exit_code: number;
}

// ---------------------------------------------------------------------------
// plugins.list(myia plugin list --json 等价)
// ---------------------------------------------------------------------------

export interface PluginsListParams {
  /** 安装根目录(缺省 = 默认安装根) */
  dir?: string;
}

export interface PluginFinding {
  severity: "error" | "warning" | string;
  scope: string;
  code: string;
  message: string;
  detail?: unknown;
}

export interface InstalledPluginEntry {
  id: string;
  dir_name: string;
  loaded: boolean;
  path: string;
  name: string | null;
  version: string | null;
  compatible: string | null;
  compatible_current: boolean;
  /** v1.1 分级:desktop | remote | server-only(manifest 缺失时 null) */
  tier: string | null;
  requires: string[];
  provides: string[];
  modes: Record<string, unknown> | null;
  install_source: string | null;
  findings: PluginFinding[];
}

export interface PluginListResult {
  command: "plugin";
  action: "list";
  dir: string;
  myia_version: string;
  plugins: InstalledPluginEntry[];
  summary: {
    installed: number;
    usable: number;
    tiers: Record<string, number>;
    errors: number;
    warnings: number;
  };
}

// ---------------------------------------------------------------------------
// doctor(myia doctor --json 等价;问题全在 findings,完成即 0)
// ---------------------------------------------------------------------------

export interface DoctorParams {
  /** 显式指定要诊断的品类 YAML(缺省 = 扫描插件目录) */
  yamls?: string[];
  plugins_dir?: string;
  db?: string;
  /** 全局代理池 YAML(--config) */
  config?: string;
  /** 代理探测超时秒数 */
  probe_timeout?: number;
}

export interface EnrichSection {
  enabled: boolean;
  model: string;
  scores: string[];
  batch: number;
  cache: boolean;
  budget_per_run: number;
  /** enrich_cache 行数;存储不可用为 null */
  cache_rows?: number | null;
}

export interface DoctorPluginReport extends PluginReport {
  /** 调度下次触发时间(ISO,品类时区换算);计算失败为 null */
  next_fire_at: string | null;
  enrich: EnrichSection | null;
}

export interface CredentialEntry {
  kind: "env" | "keychain";
  name: string;
  ref: string;
  paths: { plugin: string; path: string }[];
  plugins: string[];
  /** true/false=存在性;null=无法核验(无钥匙链后端) */
  exists: boolean | null;
  /** keychain 引用名非规范形式(myia/<scope>/<name>)时的迁移提示 */
  note?: string;
}

export interface ProxyPoolStatus {
  pool?: string;
  ok?: boolean;
  latency_seconds?: number | null;
  message?: string;
  error_type?: string;
  [key: string]: unknown;
}

export interface Finding {
  severity: "error" | "warning";
  scope: string;
  code: string;
  message: string;
}

export interface DoctorResult {
  command: "doctor";
  generated_at: string;
  db: string;
  /** healthy = 无 error 级 finding */
  healthy: boolean;
  plugins: DoctorPluginReport[];
  credentials: {
    backend_available: boolean;
    backend_error: string | null;
    entries: CredentialEntry[];
  };
  proxy: {
    config: string | null;
    pools: ProxyPoolStatus[];
    /** 全局 pools YAML 载入失败时的结构化明细 */
    error?: LoadErrorDetail[];
  };
  findings: Finding[];
  summary: {
    plugins: number;
    sources: number;
    errors: number;
    warnings: number;
  };
}

// ---------------------------------------------------------------------------
// run.start / run.status / logs.tail
// ---------------------------------------------------------------------------

export interface RunStartParams {
  /** 品类 YAML 路径(必填) */
  yaml: string;
  /** dry-run(缺省 false) */
  dry?: boolean;
  /** SQLite 库路径(缺省 = 默认库) */
  db?: string;
  /** 全局 pools YAML(--config) */
  config?: string;
}

export interface RunStartResult {
  run_id: number;
  state: "running";
  yaml: string;
  dry: boolean;
  db: string;
}

export type RunState = "running" | "done";

/** 子进程退出码语义(CLI 契约):0 success / 1 config_error / 2 failed / 3 partial */
export type RunExitStatus = "success" | "config_error" | "failed" | "partial";

export interface RunRecord {
  run_id: number;
  category: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  stats: Record<string, unknown> | null;
  steps: Record<string, unknown> | null;
  error: string | null;
}

export interface RunEntry {
  run_id: number;
  yaml: string;
  db: string;
  dry: boolean;
  state: RunState;
  exit_code: number | null;
  status: RunExitStatus | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  record: RunRecord | null;
  /** 工作线程兜底异常(completed 事件同字段) */
  error?: string;
}

export interface RunStatusParams {
  /** 缺省 = 全部 run(新→旧);未知 id = run_not_found 结构化错误 */
  run_id?: number;
}

export interface RunStatusResult {
  runs: RunEntry[];
}

export interface LogLine {
  seq: number;
  ts: string;
  run_id: number | null;
  stream: "stdout" | "stderr";
  line: string;
}

export interface LogsTailParams {
  /** tail 上限(缺省 200,硬上限 = 环形缓冲容量 4000) */
  lines?: number;
  /** 按 run 过滤(缺省 = 全部) */
  run_id?: number;
}

export interface LogsTailResult {
  lines: LogLine[];
  total: number;
  truncated: boolean;
}

// ---------------------------------------------------------------------------
// store.items(SQLiteStore.list_items 直读)
// ---------------------------------------------------------------------------

export interface StoreItemsParams {
  db?: string;
  /** 按品类过滤 */
  category?: string;
  /** ISO 时间下界 */
  since?: string;
  /** 条数上限(正整数) */
  limit?: number;
}

export interface FeedItem {
  id: number | null;
  url: string;
  dedup_key: string;
  title: string;
  source: string | null;
  /** 净化摘要(非全文) */
  content: string | null;
  /** 图析摘要(metadata.image_ocr 的单行截断源;10-03-vision-pipeline:
   *  采集图片 OCR 产物。无图条目无此键 = feed 屏零渲染变化。 */
  image_ocr?: string | null;
  tags: string[];
  category: string | null;
  scores: Record<string, unknown> | null;
  pushed_at: string | null;
  push_slot: string | null;
  first_seen: string | null;
}

export interface StoreItemsResult {
  db: string;
  count: number;
  items: FeedItem[];
}

// ---------------------------------------------------------------------------
// secret.set / secret.list(凭据只进系统钥匙链;值零回显零落日志)
// ---------------------------------------------------------------------------

export interface SecretSetParams {
  /** 凭据名(myia/<scope>/<name> 规范形式) */
  name: string;
  /** 凭据值——只经协议写入钥匙链,协议流/日志零落值 */
  value: string;
}

export interface SecretSetResult {
  name: string;
  stored: true;
}

export interface SecretListResult {
  /** 只有名字,值永不可读 */
  names: string[];
}

/** 无参方法(secret.list)的空参数 */
export interface EmptyParams {}

// ---------------------------------------------------------------------------
// image.config.*(看图结构配置;10-03-vision-pipeline 拆屏后 image.* 仅余此二方法:
// image.import/ocr/analyze/status 与 image.progress/completed 事件已随看图屏拆除,
// 图片理解并入情报管线 —— 契约权威 entry.py `_HANDLERS` 双侧同步)
// ---------------------------------------------------------------------------

/** OCR 引擎:vision = macOS Vision(ocrmac,默认)/ rapidocr = RapidOCR(onnxruntime) */
export type OcrEngine = "vision" | "rapidocr";

/** 二级看图通道:local = OpenAI 兼容本地端点(mlx-vlm/LM Studio)/ cloud = 云端视觉 API */
export type VisionChannel = "local" | "cloud";


/** 看图结构配置(vision.yaml;MYIA_HOME 第一个全局配置文件)。
 *  铁律:api_key 只收 keychain:/env: 引用,明文凭据拒载(security-baseline)。 */
export interface VisionConfig {
  channel_default: VisionChannel;
  local: {
    /** OpenAI 兼容本地端点(mlx-vlm :8080 / LM Studio :1234) */
    base_url: string;
    /** 本地模型 = 模型路径(mlx-vlm 契约:model 字段即路径) */
    model: string;
  };
  cloud: {
    base_url: string;
    /** 默认 glm-4.6v(grill 拍板;glm-4.5v 错读勿用) */
    model: string;
    /** keychain:myia/image/api_key 引用或 null;明文不落盘不回显 */
    api_key: string | null;
  };
  ocr: {
    enabled: boolean;
    engine_default: OcrEngine;
  };
}

export interface ImageConfigSaveResult {
  ok: true;
}

/** image.config.read 应答:脱敏配置外层包装(Python 侧 entry.py 锁定形状;
 *  exists=false = 文件未建 = 合法未配置态,config 恒为全缺省)。 */
export interface ImageConfigReadResult {
  file: string;
  exists: boolean;
  config: VisionConfig;
}

/** image.config.save 的参数:整份配置同门校验,失败零写入 */
export interface ImageConfigSaveParams {
  config: VisionConfig;
}

// ---------------------------------------------------------------------------
// 方法 ↔ 参数/结果 映射(entry.py `_HANDLERS` 全集)
// ---------------------------------------------------------------------------

export interface SidecarProtocol {
  version: { params: VersionParams; result: VersionResult };
  health: { params: HealthParams; result: HealthResult };
  "plugins.list": { params: PluginsListParams; result: PluginListResult };
  doctor: { params: DoctorParams; result: DoctorResult };
  "run.start": { params: RunStartParams; result: RunStartResult };
  "run.status": { params: RunStatusParams; result: RunStatusResult };
  "logs.tail": { params: LogsTailParams; result: LogsTailResult };
  "store.items": { params: StoreItemsParams; result: StoreItemsResult };
  "secret.set": { params: SecretSetParams; result: SecretSetResult };
  "secret.list": { params: EmptyParams; result: SecretListResult };
  "image.config.read": { params: EmptyParams; result: ImageConfigReadResult };
  "image.config.save": { params: ImageConfigSaveParams; result: ImageConfigSaveResult };
}

export type SidecarMethod = keyof SidecarProtocol;

// ---------------------------------------------------------------------------
// 流式事件(无 id 行,壳转发为 `sidecar://event`)
// ---------------------------------------------------------------------------

export interface LogEvent {
  type: "log";
  run_id: number;
  stream: "stdout" | "stderr";
  line: string;
  ts: string;
}

/** 进度阶段(_PROGRESS_PATTERNS 的 phase 名) */
export type ProgressPhase = "run_start" | "source_done" | "fetch_done" | "run_end";

export interface ProgressEvent {
  type: "progress";
  run_id: number;
  phase: ProgressPhase;
  ts: string;
  /** 字段由日志正则逐 phase 抽取(值均为字符串);文案变化时优雅退化缺省 */
  category?: string;
  pipeline_run_id?: string;
  sources?: string;
  dry_run?: string;
  source?: string;
  engine?: string;
  items?: string;
  source_failures?: string;
  run_status?: string;
}

export interface CompletedEvent {
  type: "completed";
  run_id: number;
  exit_code: number | null;
  status: RunExitStatus | null;
  dry: boolean;
  duration_ms?: number;
  record?: RunRecord | null;
  /** 工作线程兜底异常时存在 */
  error?: string;
  ts: string;
}

export type SidecarEvent = LogEvent | ProgressEvent | CompletedEvent;

// 看图事件流(image.progress / image.completed)已随看图屏拆除
// (10-03-vision-pipeline 拍板①:SidecarEvent 只余 run 域三事件)。