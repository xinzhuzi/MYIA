/**
 * MYIA 桌面 sidecar 协议类型(权威定义:desktop/entry.py 模块注释)。
 *
 * 线协议 = 行分隔 JSON-RPC 子集:
 *   请求  {"id", "method", "params"} → 应答 {"id", "result"} | {"id", "error"}
 *   错误  {"code", "path", "message", "data?"}(code/path/message 必有)
 *   事件  无 id、以 "type" 字段区分:log / progress / completed / test.completed,
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
  /** 协议版本(PROTOCOL_VERSION,当前 3) */
  protocol: number;
  /** 壳层 .app/bundle 版本(C10;main.rs spawn 注入 MYIA_APP_VERSION,
   *  单一事实源 = tauri.conf.json version)。dev/CLI 场景未注入 = null(如实)。 */
  app_version?: string | null;
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

/** 子进程退出码语义(CLI 契约):0 success / 1 config_error / 2 failed / 3 partial;
 * cancelled = run.cancel 信号终局(C2,只经取消路径出现)。 */
export type RunExitStatus = "success" | "config_error" | "failed" | "partial" | "cancelled";

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

// run.cancel(C2:进行中 run 的取消通道;进程组杀,SIGTERM→5s→SIGKILL)
export interface RunCancelParams {
  /** 缺省 = 当前活跃 run;未知 id / 无活跃 run = run_not_found */
  run_id?: number;
}

export interface RunCancelResult {
  run_id: number;
  cancelled: true;
  /** 取消请求受理时的注册表态;终态经 completed 事件 / run.status 可见 */
  state: "running";
}

// runs.list(C3:runs 表直读,新→旧;sidecar 重启后历史仍可达)
export interface RunsListParams {
  db?: string;
  /** 按品类过滤 */
  category?: string;
  /** 缺省 50,服务端钳制 [1,200] */
  limit?: number;
}

export interface RunsListResult {
  db: string;
  count: number;
  runs: RunRecord[];
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
  /** ISO 时间下界(first_seen ≥,含边界) */
  since?: string;
  /** 翻页游标(first_seen 严格小于;与 before_id 组成复合游标) */
  before?: string;
  /** 复合游标第二键:同刻(相同 first_seen)条目超单页 limit 也能推进取尽;
   *  需与 before 同传 */
  before_id?: number;
  /** title/content/source 三列 LIKE(大小写不敏感;%/_ 按字面) */
  query?: string;
  /** 条数上限(正整数) */
  limit?: number;
}

/** OCR 逐行结果(myia.vision.ocr OcrLine 投影;conf 0-1,两引擎刻度不可互比) */
export interface ImageOcrLine {
  text: string;
  conf: number;
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
  /** 配图视觉描述全文(metadata.image_caption;10-03-vision-v2 起随 VL 通道
   *  产生,详情展开态全文呈现)。空白/类型不符后端已置 None。 */
  image_caption?: string | null;
  /** 落图文件绝对路径清单(metadata.image_files;内容寻址不重复落盘)。
   *  详情态先以路径文本列表呈现,图片本尊显示属 v2.2。 */
  image_files?: string[] | null;
  /** OCR 逐行 {text, conf}(metadata.image_ocr_lines;详情展开逐行置信度表)。
   *  conf 0-1 原样透传,两引擎刻度不可互比 —— 色阶只是视觉提示。 */
  image_ocr_lines?: ImageOcrLine[] | null;
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

// feed.export(G3,10-03-feed-ux:当前过滤视图导出 JSONL/CSV,sidecar 直写;
// 契约钉死于任务档 design.md §1,与 entry.py `_m_feed_export` 互指)
export interface FeedExportParams {
  /** 导出格式 */
  format: "jsonl" | "csv";
  /** 绝对路径(前端 dialog.save() 用户选定;覆盖确认归对话框) */
  path: string;
  /** 当前过滤视图的品类(与 store.items 同一查询面) */
  category?: string;
  /** 当前过滤视图的搜索词(title/content/source 三列 LIKE NOCASE) */
  query?: string;
  db?: string;
}

export interface FeedExportResult {
  /** 实际写入的路径(resolve 后) */
  path: string;
  /** 导出条目数(CSV 表头不计) */
  count: number;
  /** 文件字节数 */
  bytes: number;
}

// feed.enrich(G8,10-03-fe-small-batch:单条情报卡 AI 摘要/精评,骑既有 enrich
// 管线 myia.enrich.LLMEnricher 现跑;与 entry.py `_m_feed_enrich` 互指)
export interface FeedEnrichParams {
  /** 条目引用:items.id(int)或 dedup_key/URL(str);同 resolve_item_ref
   *  (feedback.mark 口径) */
  item: string | number;
  db?: string;
}

export interface FeedEnrichResult {
  /** 精评条目行 id(resolve 命中行;可为 null 的协议余量) */
  item_id: number | null;
  /** 品类 YAML enrich 节配置的模型名 */
  model: string;
  /** 维度分 {维度: 分值}(LLMEnricher 原路回填 items 表) */
  scores: Record<string, unknown>;
  /** 复合标量分(metadata["score"];route 依赖位) */
  score: number;
  /** enrich_cache 命中((url, model, scores_key) 命中零 token) */
  cached: boolean;
}

// schedule.preview(G4,10-03-feed-ux:品类排程 Next runs 预览,纯计算零副作用;
// 与 entry.py `_m_schedule_preview` 互指)
export interface SchedulePreviewParams {
  /** 品类 YAML 路径(围栏:必须位于 plugins 目录内) */
  file: string;
  /** 预览次数(缺省 5,服务端钳制 [1,20]) */
  count?: number;
}

export interface SchedulePreviewResult {
  file: string;
  /** 5 段 cron 原文;无排程品类明示 null(不是错误) */
  schedule: string | null;
  /** IANA 时区名;null = 系统时区 */
  timezone: string | null;
  /** 未来运行时刻(ISO-8601,新→远) */
  runs: string[];
}

// push.test(G5 前半,10-03-feed-ux:合成单条测试条目真发指定通道;
// 与 entry.py `_m_push_test` 互指)
export interface PushTestParams {
  /** 通道名(= 品类 YAML push[].channel 同一注册表) */
  channel: string;
  /** 凭据引用(env:/keychain:);缺省走通道默认 env 引用链 */
  target?: string;
  /** 用户模板(Jinja2;一般测试不带) */
  template?: string;
}

export interface PushTestResult {
  ok: true;
  channel: string;
  /** 仅 stdout 通道:卡片行文本(serve 模式协议流零污染) */
  preview?: string;
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

// secret.delete(C5:误存凭据的 UI 清除口;二次删除 = secret_not_found)
export interface SecretDeleteParams {
  name: string;
}

export interface SecretDeleteResult {
  name: string;
  deleted: true;
}

// sources.test(C13:试抓此源,异步 job;结果走 test.completed 事件)
export interface SourcesTestParams {
  /** 品类 YAML 路径(围栏:必须位于 plugins 目录内) */
  file: string;
  /** 缺省 = 全部源;UI 只用单源形态 */
  source?: string;
  /** 每源超时秒数(≤120,同壳层单请求硬超时) */
  timeout?: number;
  /** 全局 pools YAML(--config) */
  config?: string;
}

export interface SourcesTestResult {
  job_id: number;
  state: "running";
  source?: string;
}

/** test.completed 事件载荷(C13;ok=false 时 error/data 携 CLI 结构化错) */
export interface TestCompletedEvent {
  type: "test.completed";
  job_id: number;
  ok: boolean;
  exit_code: number | null;
  /** CLI `myia test --json` 报文(command/yaml/sources[]/status) */
  result?: Record<string, unknown>;
  /** 失败族:error = CLI 报文的 error 字段(config 等) */
  error?: string;
  data?: Record<string, unknown>;
  ts: string;
}

// ---------------------------------------------------------------------------
// feedback.*(B2,10-03-v112-desktop-parity:桌面反馈入口;与 CLI myia feedback
// 同门直调 myia.feedback —— channel="desktop" 落库,CLI list 无过滤即见,
// 往返一致;载荷键逐一对齐 cli.py `_feedback_row_dict` / stats 报文)
// ---------------------------------------------------------------------------

/** feedback.mark:卡片 👍/👎 → record_feedback(channel=desktop) */
export interface FeedbackMarkParams {
  /** 条目引用:items.id(int)或 dedup_key/URL(str);同 resolve_item_ref */
  item: string | number;
  /** 结论:good=👍 / bad=👎(normalize_verdict 同门) */
  verdict: "good" | "bad";
  db?: string;
}

export interface FeedbackMarkResult {
  feedback_id: number | null;
  item_id: number | null;
  dedup_key: string;
  verdict: "good" | "bad";
  channel: string;
}

/** feedback.list:反馈记录(新→旧);键同 CLI _feedback_row_dict */
export interface FeedbackListParams {
  verdict?: "good" | "bad";
  channel?: string;
  /** 缺省 50 */
  limit?: number;
  db?: string;
}

export interface FeedbackRow {
  id: number | null;
  item_id: number | null;
  dedup_key: string;
  verdict: string;
  channel: string;
  title: string | null;
  category: string | null;
  created_at: string | null;
}

export interface FeedbackListResult {
  count: number;
  items: FeedbackRow[];
}

/** FeedbackStats.to_dict()(CLI stats --json 同形) */
export interface FeedbackStatsPayload {
  total: number;
  good: number;
  bad: number;
  /** bad/(good+bad),四位小数;零反馈 = 0 */
  bad_ratio: number;
  by_channel: Record<string, number>;
  /** 负反馈 Top 类目(降序;{key, bad}) */
  top_bad_categories: { key: string; bad: number }[];
  /** 负反馈 Top 词条(降序;{key, bad}) */
  top_bad_words: { key: string; bad: number }[];
}

/** feedback.stats:窗口统计 + 生效调参 + 调参历史(键同 CLI stats 载荷) */
export interface FeedbackStatsParams {
  /** 统计窗口天数(缺省 14;TuningPolicy 同门) */
  window_days?: number;
  /** Top N(缺省 5) */
  top?: number;
  db?: string;
}

export interface FeedbackStatsResult {
  window_days: number;
  stats: FeedbackStatsPayload;
  /** 生效调参(ActiveTuning.to_dict();enrich 关闭时仅入库不生效) */
  active_tuning: Record<string, unknown>;
  /** 最近调参历史(list_tuning(limit=10)) */
  tuning_history: {
    id: number | null;
    kind: string;
    payload: Record<string, unknown>;
    created_at: string | null;
  }[];
}

// ---------------------------------------------------------------------------
// store.trend(B4,10-03-v112-desktop-parity:采集量趋势,items 按 first_seen
// UTC 逐日计数;口径 = UTC 逐日,不做时区换算,卡面如实注记)
// ---------------------------------------------------------------------------

export interface StoreTrendParams {
  /** 窗口天数(缺省 14,服务端钳制 [1,90]) */
  days?: number;
  category?: string;
  db?: string;
}

export interface TrendDay {
  /** YYYY-MM-DD(UTC) */
  date: string;
  count: number;
}

export interface StoreTrendResult {
  days: TrendDay[];
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
 *  铁律:api_key 只收 keychain: 引用(env: 同拒),明文凭据拒载(security-baseline)。 */
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
// image.models.* / image.server.*(10-03-vision-v2:模型下载与 server 代管;
// 契约与 entry.py `_m_image_models_*` / `_m_image_server_*` 同形状冻结,
// 能力实现 src/myia/vision/models.py / server.py)
// ---------------------------------------------------------------------------

/** 已装模型(image.models.list 逐项;模型 = models/ 一级子目录) */
export interface VisionModelEntry {
  /** 目录名(下载时 repo 名段或自定义本地名) */
  name: string;
  /** resolve 后的绝对路径 */
  path: string;
  /** 递归字节数(跳过隐藏 .cache;未完下载不算已装体量) */
  bytes: number;
  /** = 目录与 vision.yaml local.model resolve 后全等(当前激活) */
  active: boolean;
  /** 半成品(缺 config.json 或 *.safetensors):激活被拒 model_incomplete;
   *  再次 download 同名 = 断点续传补全 */
  incomplete: boolean;
}

export interface ImageModelsListResult {
  /** 空目录 = 合法空表(UI 给下载引导不报错) */
  models: VisionModelEntry[];
}

/** repo 合法形(镜像 models.py `_REPO_RE`):mlx-community/<name>,单斜杠,org 固定 */
export const IMAGE_MODELS_REPO_RE = /^mlx-community\/[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** 本地名合法形(镜像 `_NAME_RE`;禁路径分隔,防穿越) */
export const IMAGE_MODELS_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export interface ImageModelsDownloadParams {
  /** HF 仓库全名,必须 mlx-community/<name>(MLX 格式权重直下免 convert;
   *  其余命名空间多为原始 HF 权重,对 mlx_vlm.server 不可用,结构化拒) */
  repo: string;
  /** 本地目录名;缺省 = repo 名段 */
  name?: string;
}

export interface ImageModelsDownloadResult {
  /** 异步 job id(进度/终态走 image.models.progress / completed 两事件) */
  job_id: number;
}

export interface ImageModelsDeleteParams {
  name: string;
}

export interface ImageModelsActivateParams {
  name: string;
}

export interface ImageModelsMutationResult {
  ok: true;
}

/** image.server.status / image.server.ensure 公共形状(server.py `vision_server_status`) */
export interface ImageServerStatusResult {
  /** 端口有进程在听(拿到 HTTP 应答;连接拒绝/超时 = false) */
  running: boolean;
  /** vision.yaml local.base_url 配置值(探测目标) */
  base_url: string;
  /** vision.yaml local.model 配置值(配置口径,不问 server 实载) */
  model: string;
  /** GET {base_url}/models 返回 200 */
  healthy: boolean;
}

/** image.server.ensure 应答 = status + started;慢路径另带 ensuring/job_id。
 *  已健康(快路径)= status + {started:false},零后台零事件(冻结契约原样);
 *  需自起(慢路径)= 快照超集 + {ensuring:true, job_id},应答立即返回,
 *  自起 + 健康等待(≤120s)跑 sidecar 后台线程,终态走
 *  ImageServerCompletedEvent 事件;并发第二单 = ensure_busy。 */
export interface ImageServerEnsureResult extends ImageServerStatusResult {
  started: boolean;
  /** 慢路径标记:终态经 image.server.completed 事件收口 */
  ensuring?: boolean;
  /** 慢路径 job id(image.server.completed 事件同 id) */
  job_id?: number;
}

// image.files.purge(10-03-vision-v2 复查:落图零回收的 CLI 面清除口;UI 不做)
export interface ImageFilesPurgeParams {
  /** 按文件 mtime 清超龄落图的天数(≥1 整数) */
  days: number;
}

export interface ImageFilesPurgeResult {
  deleted: number;
  bytes_freed: number;
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
  "run.cancel": { params: RunCancelParams; result: RunCancelResult };
  "runs.list": { params: RunsListParams; result: RunsListResult };
  "logs.tail": { params: LogsTailParams; result: LogsTailResult };
  "store.items": { params: StoreItemsParams; result: StoreItemsResult };
  "feed.export": { params: FeedExportParams; result: FeedExportResult };
  "feed.enrich": { params: FeedEnrichParams; result: FeedEnrichResult };
  "schedule.preview": { params: SchedulePreviewParams; result: SchedulePreviewResult };
  "secret.set": { params: SecretSetParams; result: SecretSetResult };
  "secret.list": { params: EmptyParams; result: SecretListResult };
  "secret.delete": { params: SecretDeleteParams; result: SecretDeleteResult };
  "sources.test": { params: SourcesTestParams; result: SourcesTestResult };
  "push.test": { params: PushTestParams; result: PushTestResult };
  "feedback.mark": { params: FeedbackMarkParams; result: FeedbackMarkResult };
  "feedback.list": { params: FeedbackListParams; result: FeedbackListResult };
  "feedback.stats": { params: FeedbackStatsParams; result: FeedbackStatsResult };
  "store.trend": { params: StoreTrendParams; result: StoreTrendResult };
  "image.config.read": { params: EmptyParams; result: ImageConfigReadResult };
  "image.config.save": { params: ImageConfigSaveParams; result: ImageConfigSaveResult };
  "image.models.list": { params: EmptyParams; result: ImageModelsListResult };
  "image.models.download": { params: ImageModelsDownloadParams; result: ImageModelsDownloadResult };
  "image.models.delete": { params: ImageModelsDeleteParams; result: ImageModelsMutationResult };
  "image.models.activate": { params: ImageModelsActivateParams; result: ImageModelsMutationResult };
  "image.server.status": { params: EmptyParams; result: ImageServerStatusResult };
  "image.server.ensure": { params: EmptyParams; result: ImageServerEnsureResult };
  "image.files.purge": { params: ImageFilesPurgeParams; result: ImageFilesPurgeResult };
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

/** 模型下载进度事件(entry.py `_image_models_download_worker`;后端 0.5s 节流,
 *  终态前必发最后一次)。total_bytes 未知(HF 未回报)时缺省。 */
export interface ImageModelsProgressEvent {
  type: "image.models.progress";
  job_id: number;
  repo: string;
  done_bytes: number;
  total_bytes?: number;
  ts: string;
}

/** 模型下载终态事件:ok=false 时 error = 结构化 code(disk_insufficient /
 *  hf_unavailable / 网络失败族等;见 myia.vision.models 错误码表)。 */
export interface ImageModelsCompletedEvent {
  type: "image.models.completed";
  job_id: number;
  ok: boolean;
  error?: string;
  ts: string;
}

/** 本地 server ensure 终态事件(entry.py `_image_server_ensure_worker`;
 *  image.server.ensure 慢路径应答后的收口)。ok=true 时 status =
 * status+{started} 全量;ok=false 时 error = myia.vision.server 错误族
 * code(no_local_model / spawn_failed / server_start_failed 等)。 */
export interface ImageServerCompletedEvent {
  type: "image.server.completed";
  job_id: number;
  ok: boolean;
  status?: ImageServerEnsureResult;
  error?: string;
  ts: string;
}

export type SidecarEvent =
  | LogEvent
  | ProgressEvent
  | CompletedEvent
  | TestCompletedEvent
  | ImageModelsProgressEvent
  | ImageModelsCompletedEvent
  | ImageServerCompletedEvent;

// 看图事件流:image.progress / image.completed 已随看图屏拆除
// (10-03-vision-pipeline 拍板①);10-03-vision-v2 起新增模型下载域两事件
// (image.models.progress / completed)与 server ensure 终态事件
// (image.server.completed,ensure 慢路径应答即返、终态走事件),
// SidecarEvent = run 域三事件 + test.completed + 模型下载域两事件 +
// server ensure 终态事件。
