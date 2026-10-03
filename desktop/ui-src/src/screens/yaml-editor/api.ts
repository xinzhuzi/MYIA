/**
 * 配置编辑数据装配(本屏私有 api 模块;共享客户端 @/lib/api 只读不动,
 * 惯例与 screens/sources/api.ts 同款:屏私有封装 + invoke("sidecar_request") +
 * asSidecarError 归一化)。
 *
 * 数据面 = sidecar 协议 yaml.* 六方法(task 10-03-yaml-editor,契约钉死于
 * 任务档 design.md §1;Python 侧实现 = desktop/entry.py `_m_yaml_*` 六处理器,
 * 两侧注释互指):
 *   yaml.list     plugins 目录扫描(坏文件也入列,parse_ok=false 修好它是本屏核心用例)
 *   yaml.read     原文直读(newline="" 逐字节保真,注释/顺序原样)
 *   yaml.validate 干跑校验(load_category 同门;findings 分级 error/warning,零写入)
 *   yaml.template 最小合法品类模板(id/name 占位,UI 回填)
 *   yaml.save     同门校验→跨文件 id 查重→.bak→原子写(mtime 乐观锁;
 *                 file 不存在 + expected_mtime=null = 新建)
 *   yaml.delete   .bak 留底→删主文件→连带删 .disabled.json
 *
 * 错误码族(path_outside_root/not_yaml_suffix/invalid_file_stem/file_too_large/
 * file_not_found/mtime_conflict/duplicate_category_id/category_invalid/
 * source_write_failed/source_dir_unreadable)由后端结构化抛出,本模块只归一化
 * 为 SidecarRequestError,界面按 code 如实展示。
 */
import { invoke } from "@tauri-apps/api/core";

import { SidecarRequestError } from "@/lib/api";
import type { SidecarErrorShape } from "@/lib/api";

// ---------------------------------------------------------------------------
// 契约类型(逐字段对照 design.md §1 / entry.py `_m_yaml_*`;勿凭记忆增删)
// ---------------------------------------------------------------------------

/** 坏文件的加载失败明细(yaml.list 单项;形状与 load_category 的 error 同构) */
export interface YamlFileError {
  path: string;
  code: string;
  message: string;
}

/** yaml.list 单项:解析状态 + 品类元信息(坏文件也入列) */
export interface YamlFileEntry {
  /** 品类 YAML 绝对路径(sources.write / 编辑读写同一口径) */
  file: string;
  /** 文件名(如 ai-news.yaml) */
  name: string;
  /** yaml 语法 + load_category 同门试载 */
  parse_ok: boolean;
  category_id: string | null;
  category_name: string | null;
  /** 启用源数(parse_ok 时) */
  sources: number | null;
  error: YamlFileError | null;
}

export interface YamlListResult {
  plugins_dir: string;
  files: YamlFileEntry[];
}

export interface YamlReadResult {
  file: string;
  /** UTF-8 原文(不经 dump 往返,注释/顺序逐字节原样) */
  content: string;
  size: number;
  /** save 乐观锁基线 */
  mtime: number;
}

/** 校验 finding:level=error 拦保存;warning(如 secret_unknown)放行且 save 应答带回 */
export interface YamlFinding {
  /** 字段路径("$" 起,如 $.sources[0].url) */
  path: string;
  code: string;
  message: string;
  level: "error" | "warning";
}

export interface YamlValidateResult {
  /** = 无 error 级 finding(warning 不翻假) */
  valid: boolean;
  findings: YamlFinding[];
  /** 装载成功时的品类摘要(语法/schema 错时为 null) */
  category: { id: string; name: string; sources: number } | null;
}

export interface YamlSaveResult {
  file: string;
  written: true;
  /** 新建(file 不存在 + expected_mtime=null)为 true */
  created: boolean;
  /** .bak 路径(新建无此步 → null) */
  backed_up: string | null;
  /** 写盘后的新基线(下次 save 的乐观锁对照值) */
  mtime: number;
  /** warning 级 findings 原样带回(UI 展示;secret_unknown 这类不拦保存) */
  warnings: YamlFinding[];
}

export interface YamlDeleteResult {
  file: string;
  deleted: true;
  backed_up: string;
}

export interface YamlTemplateResult {
  content: string;
}

// ---------------------------------------------------------------------------
// 新建文件名预检(与后端围栏同源)
// ---------------------------------------------------------------------------

/**
 * 品类 id 正则的 TS 镜像(权威定义:src/myia/schema.py `CATEGORY_ID_RE`,
 * `^[a-z0-9][a-z0-9_-]{0,63}$`;entry.py 新建围栏 import 同一常量)。
 * 前端只做「失败即拦不发请求」的预检,后端围栏仍是最终守门 —— 两处语义
 * 必须一致,改 schema 正则时此处同步。
 */
export const CATEGORY_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** 新建文件 stem 预检(小写字母/数字/-/_,1-64 字符;中文名放 name: 字段) */
export function isValidFileStem(stem: string): boolean {
  return CATEGORY_ID_PATTERN.test(stem);
}

// ---------------------------------------------------------------------------
// 传输封装(与共享 client 同壳命令,错误归一化逻辑一致;独立实现避免动共享层)
// ---------------------------------------------------------------------------

/** 任意抛出物 → SidecarRequestError(规则与 sources/api.ts asSidecarError 相同)。 */
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

/** 类型化往返:yaml.* 六方法经壳命令 sidecar_request 调用,失败归一化抛出。 */
async function yamlRequest<R>(method: string, params: unknown): Promise<R> {
  try {
    return await invoke<R>("sidecar_request", { method, params });
  } catch (raw) {
    throw asSidecarError(raw);
  }
}

/** 品类 YAML 清单(目录由 serve 上下文定;坏文件 parse_ok=false 也入列)。 */
export function listYamlFiles(): Promise<YamlListResult> {
  return yamlRequest<YamlListResult>("yaml.list", {});
}

/** 原文直读(注释/顺序逐字节原样;mtime 为 save 乐观锁基线)。 */
export function readYaml(file: string): Promise<YamlReadResult> {
  return yamlRequest<YamlReadResult>("yaml.read", { file });
}

/**
 * 干跑校验(YAML 语法 + load_category 同门 + keychain: 引用对照;
 * 校验结果本身是正常应答,只有参数类型错才抛)。file 仅作错误上下文,可传 null。
 */
export function validateYaml(content: string, file: string | null): Promise<YamlValidateResult> {
  return yamlRequest<YamlValidateResult>("yaml.validate", { content, file });
}

/** 最小合法品类模板(id/name 占位,UI 按用户输入回填)。 */
export function getYamlTemplate(): Promise<YamlTemplateResult> {
  return yamlRequest<YamlTemplateResult>("yaml.template", {});
}

/**
 * 编辑写回:同门校验 error 级零容忍零写入,warning 带回;跨文件 id 查重;
 * 旧文件先 .bak 留底再原子落盘。
 * expected_mtime:已有文件 = read 带回的基线(乐观锁);新建 = null。
 */
export function saveYaml(file: string, content: string, expected_mtime: number | null): Promise<YamlSaveResult> {
  return yamlRequest<YamlSaveResult>("yaml.save", { file, content, expected_mtime });
}

/** 删除品类文件(.bak 留底;连带删 .disabled.json 暂存)。 */
export function deleteYaml(file: string): Promise<YamlDeleteResult> {
  return yamlRequest<YamlDeleteResult>("yaml.delete", { file });
}
