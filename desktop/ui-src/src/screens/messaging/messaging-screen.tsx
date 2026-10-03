import { MessageCircle, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { SidecarRequestError } from "@/lib/api";

import { ErrorBox } from "../sources/error-box";
import {
  asSidecarError,
  channelsAliasDelete,
  channelsAliasSet,
  channelsList,
  channelsRefresh,
  formatLastSeen,
  hasAlias,
  isDeadEntry,
  listSecretNames,
  pushWrite,
  targetSpec,
} from "./api";
import type { ChannelEntry, ChannelsView, PushRuleEntry, PushRuleFile } from "./api";
import { PlatformOverview } from "./platform-overview";

interface LoadState {
  status: "loading" | "error" | "ready";
  data: ChannelsView | null;
  error: SidecarRequestError | null;
}

/** 行内别名编辑态(一次只开一行:platform+chat_id 定位)。 */
interface AliasDraft {
  platform: string;
  chatId: string;
  value: string;
}

/** 顶部结构化提示:别名/刷新/保存动作的结果(ok)或失败(error)。 */
interface Notice {
  kind: "ok" | "error";
  text: string;
}

/**
 * 消息(task 10-03-messaging-ui):平台总览 + 通道目录 + 推送规则三区布局。
 *
 * 最上区·平台总览(task 10-03-messaging-platforms):平台卡片网格 —— 已实装
 * 平台带三态徽标(已连接/需要设置,前端派生:secret.list 凭据探测 +
 * channels.list 目录信号)与可展开的出站凭据指南;W2/W3 未实装平台灰卡
 * 「即将支持」;全部/已连接/未启用三档筛选。
 * 上区·通道目录:按平台分组(名称/类型/最后发现/死信徽标),每平台一个
 * 「刷新」按钮(触发 sidecar channels.refresh → discover_directory;失败
 * toast 结构化错误,旧目录不动),别名行内编辑写 channel_aliases.json 语义。
 * 下区·推送规则:按品类 YAML 分组列出 push 条目,每条目一个 targets 多选器
 * (选项 = 上区该平台目录条目,产出 `platform:名称`),保存走 push.write
 * 全量替换(服务端同门校验,失败零写入、界面如实报错)。
 * 空态(目录为空)给「先配平台凭据」指引;断连态与现有屏同范式(ErrorBox+重试)。
 */
export function MessagingScreen() {
  const [state, setState] = useState<LoadState>({ status: "loading", data: null, error: null });
  const [refreshing, setRefreshing] = useState<Set<string>>(new Set());
  const [aliasDraft, setAliasDraft] = useState<AliasDraft | null>(null);
  const [aliasBusy, setAliasBusy] = useState(false);
  /** targets 草稿:file → entryIndex → 已选 spec 列表(缺省回退 rules 视图值)。 */
  const [drafts, setDrafts] = useState<Record<string, Record<number, string[]>>>({});
  const [savingFile, setSavingFile] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  /** 钥匙链凭据名清单(平台总览凭据探测;加载失败降级为空名单)。 */
  const [secretNames, setSecretNames] = useState<string[]>([]);

  const reload = useCallback(async () => {
    setState({ status: "loading", data: null, error: null });
    try {
      const [data, names] = await Promise.all([
        channelsList(),
        // 凭据探测(secret.list)是平台卡的次要信号:钥匙链不可用时降级为
        // 空名单,三态回退到「目录非空」单一证据,不挡整屏目录视图。
        listSecretNames().catch(() => [] as string[]),
      ]);
      setSecretNames(names);
      setState({ status: "ready", data, error: null });
    } catch (error) {
      setState({ status: "error", data: null, error: asSidecarError(error) });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const platforms = useMemo(
    () => Object.keys(state.data?.platforms ?? {}).sort(),
    [state.data],
  );

  const handleRefresh = useCallback(
    async (platform: string) => {
      setNotice(null);
      setRefreshing((prev) => new Set(prev).add(platform));
      try {
        const result = await channelsRefresh(platform);
        setNotice({ kind: "ok", text: `${platform} 目录已刷新(${result.merged} 个会话)` });
        await reload();
      } catch (error) {
        const structured = asSidecarError(error);
        setNotice({ kind: "error", text: `${platform} 刷新失败:${structured.message}(code=${structured.code})` });
      } finally {
        setRefreshing((prev) => {
          const next = new Set(prev);
          next.delete(platform);
          return next;
        });
      }
    },
    [reload],
  );

  const startAliasEdit = useCallback(
    (entry: ChannelEntry) => {
      setNotice(null);
      const current = state.data?.aliases[entry.platform]?.[entry.chat_id] ?? entry.name;
      setAliasDraft({ platform: entry.platform, chatId: entry.chat_id, value: current });
    },
    [state.data],
  );

  const submitAlias = useCallback(
    async (apply: boolean) => {
      if (!aliasDraft) return;
      if (!apply) {
        setAliasDraft(null);
        return;
      }
      const value = aliasDraft.value.trim();
      if (!value) {
        setNotice({ kind: "error", text: "别名不能为空(要清除别名请用「取消别名」)" });
        return;
      }
      setAliasBusy(true);
      try {
        await channelsAliasSet(aliasDraft.platform, aliasDraft.chatId, value);
        setNotice({ kind: "ok", text: `已命名 ${aliasDraft.platform}:${value}` });
        setAliasDraft(null);
        await reload();
      } catch (error) {
        const structured = asSidecarError(error);
        setNotice({ kind: "error", text: `别名保存失败:${structured.message}(code=${structured.code})` });
      } finally {
        setAliasBusy(false);
      }
    },
    [aliasDraft, reload],
  );

  const removeAlias = useCallback(
    async (entry: ChannelEntry) => {
      setNotice(null);
      setAliasBusy(true);
      try {
        await channelsAliasDelete(entry.platform, entry.chat_id);
        setNotice({ kind: "ok", text: `已取消 ${entry.platform}:${entry.chat_id} 的别名(回退发现名)` });
        await reload();
      } catch (error) {
        const structured = asSidecarError(error);
        setNotice({ kind: "error", text: `别名删除失败:${structured.message}(code=${structured.code})` });
      } finally {
        setAliasBusy(false);
      }
    },
    [reload],
  );

  /** 当前生效 targets:草稿优先,缺省回退 rules 视图的落盘值。 */
  const currentTargets = useCallback(
    (file: string, entry: PushRuleEntry): string[] =>
      drafts[file]?.[entry.index] ?? entry.targets,
    [drafts],
  );

  const toggleTarget = useCallback(
    (file: string, entry: PushRuleEntry, spec: string) => {
      setNotice(null);
      const current = drafts[file]?.[entry.index] ?? entry.targets;
      const next = current.includes(spec)
        ? current.filter((candidate) => candidate !== spec)
        : [...current, spec];
      setDrafts((prev) => ({
        ...prev,
        [file]: { ...(prev[file] ?? {}), [entry.index]: next },
      }));
    },
    [drafts],
  );

  const saveRuleFile = useCallback(
    async (ruleFile: PushRuleFile) => {
      setNotice(null);
      setSavingFile(ruleFile.file);
      try {
        // 全量替换契约(design.md D2):提交该文件完整 push 数组 —— raw 为
        // base,仅覆盖 targets;push.write 服务端同门校验,失败零写入。
        const nextPush = ruleFile.entries.map((entry) =>
          entry.platform
            ? { ...entry.raw, targets: currentTargets(ruleFile.file, entry) }
            : entry.raw,
        );
        const result = await pushWrite(ruleFile.file, nextPush);
        setNotice({
          kind: "ok",
          text: result.changed
            ? `已保存 ${ruleFile.category_id ?? ruleFile.file} 的推送对象`
            : `${ruleFile.category_id ?? ruleFile.file} 无变更`,
        });
        setDrafts((prev) => {
          const next = { ...prev };
          delete next[ruleFile.file];
          return next;
        });
        await reload();
      } catch (error) {
        const structured = asSidecarError(error);
        setNotice({ kind: "error", text: `保存失败:${structured.message}(code=${structured.code})` });
      } finally {
        setSavingFile(null);
      }
    },
    [currentTargets, reload],
  );

  const data = state.data;

  return (
    <div className="flex flex-col gap-4 pb-6">
      <PageHeader
        title="消息"
        description="平台总览与接入态;通道目录浏览与别名命名;给推送规则挑选具体会话(保存写回品类 YAML)"
        actions={
          <>
            {data ? (
              <Badge variant="outline" data-testid="directory-updated">
                {data.updated_at ? `目录 ${data.updated_at.slice(0, 16).replace("T", " ")}` : "目录从未刷新"}
              </Badge>
            ) : null}
            <Button size="sm" variant="outline" onClick={() => void reload()} disabled={state.status === "loading"}>
              <RefreshCw className={state.status === "loading" ? "size-3.5 animate-spin" : "size-3.5"} />
              刷新
            </Button>
          </>
        }
      />

      {state.status === "error" && state.error ? (
        <ErrorBox error={state.error} onRetry={() => void reload()} />
      ) : null}

      {notice ? (
        <div
          role={notice.kind === "error" ? "alert" : "status"}
          className={
            notice.kind === "error"
              ? "mx-6 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
              : "mx-6 rounded-md border border-ok/30 bg-ok/10 px-4 py-3 text-sm text-ok"
          }
          data-testid="messaging-notice"
        >
          {notice.text}
        </div>
      ) : null}

      {/* ---------------- 最上区:平台总览(卡片网格 + 三态 + 筛选 + 凭据指南) ---------------- */}
      <PlatformOverview
        status={state.status}
        directory={data?.platforms ?? {}}
        secretNames={secretNames}
      />

      {/* ---------------- 上区:通道目录(按平台分组) ---------------- */}
      <div className="px-6">
        <Card>
          <CardContent className="flex flex-col gap-3 p-4">
            <p className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              <MessageCircle className="size-4 text-muted-foreground" />
              通道目录
              <span className="text-xs font-normal text-muted-foreground">
                (目录 = 可寻址的推送对象;别名是手工命名,重建后仍生效)
              </span>
            </p>

            {state.status === "loading" ? (
              <div className="flex flex-col gap-2" aria-label="加载中">
                {[0, 1, 2].map((index) => (
                  <Skeleton key={index} className="h-9 w-full" />
                ))}
              </div>
            ) : state.status === "ready" && platforms.length === 0 ? (
              <EmptyState
                title="通道目录还是空的"
                description="先到「设置」录入平台凭据(经钥匙链的 bot token),再回到这里点刷新;飞书会列出 bot 所在的群,telegram 会话随 bot 收到消息自动入目录。"
                tag="可空态"
              />
            ) : (
              platforms.map((platform) => (
                <div key={platform} className="flex flex-col gap-1.5" data-testid={`platform-${platform}`}>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-medium text-foreground">{platform}</p>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={refreshing.has(platform)}
                      onClick={() => void handleRefresh(platform)}
                    >
                      <RefreshCw className={refreshing.has(platform) ? "size-3.5 animate-spin" : "size-3.5"} />
                      {refreshing.has(platform) ? "发现中…" : "刷新"}
                    </Button>
                  </div>
                  <ul className="flex flex-col gap-1">
                    {(data?.platforms[platform] ?? []).map((entry) => {
                      const aliased = data ? hasAlias(entry, data.aliases) : false;
                      const dead = data ? isDeadEntry(entry, data.dead) : false;
                      const editing =
                        aliasDraft?.platform === entry.platform && aliasDraft?.chatId === entry.chat_id;
                      return (
                        <li
                          key={`${entry.platform}:${entry.chat_id}`}
                          className="flex flex-col gap-1 rounded-md border border-border/60 px-3 py-2"
                          data-testid={`entry-${entry.chat_id}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex min-w-0 items-center gap-2 text-sm">
                              <Badge variant="outline">{entry.type}</Badge>
                              <span className="truncate font-medium text-foreground">{entry.name}</span>
                              {aliased ? <Badge variant="secondary">别名</Badge> : null}
                              {dead ? (
                                <Badge variant="destructive" title="此前投递确认不可达,重发成功后自愈">
                                  死信
                                </Badge>
                              ) : null}
                              <span className="truncate font-mono text-[11px] text-muted-foreground">
                                {entry.chat_id}
                              </span>
                            </span>
                            <span className="flex shrink-0 items-center gap-2">
                              <span className="text-[11px] text-muted-foreground">
                                最后发现 {formatLastSeen(entry.last_seen)}
                              </span>
                              {editing ? null : (
                                <>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled={aliasBusy}
                                    onClick={() => startAliasEdit(entry)}
                                  >
                                    改名
                                  </Button>
                                  {aliased ? (
                                    <Button
                                      size="sm"
                                      variant="ghost"
                                      disabled={aliasBusy}
                                      onClick={() => void removeAlias(entry)}
                                    >
                                      取消别名
                                    </Button>
                                  ) : null}
                                </>
                              )}
                            </span>
                          </div>
                          {editing && aliasDraft ? (
                            <div className="flex items-center gap-2 pl-1" role="group" aria-label="别名编辑">
                              <input
                                aria-label={`别名 ${entry.chat_id}`}
                                className="h-7 w-56 rounded-md border border-border bg-transparent px-2 text-xs"
                                value={aliasDraft.value}
                                autoFocus
                                disabled={aliasBusy}
                                onChange={(event) =>
                                  setAliasDraft({ ...aliasDraft, value: event.target.value })
                                }
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") void submitAlias(true);
                                  if (event.key === "Escape") void submitAlias(false);
                                }}
                              />
                              <Button size="sm" disabled={aliasBusy} onClick={() => void submitAlias(true)}>
                                保存
                              </Button>
                              <Button size="sm" variant="ghost" disabled={aliasBusy} onClick={() => void submitAlias(false)}>
                                取消
                              </Button>
                            </div>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/* ---------------- 下区:推送规则面板(按品类文件分组) ---------------- */}
      <div className="px-6">
        <Card>
          <CardContent className="flex flex-col gap-3 p-4">
            <p className="text-sm font-medium text-foreground">
              推送规则
              <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                (给每条规则勾选具体推送对象;保存 = 全量写回该品类 YAML 的 push[])
              </span>
            </p>

            {state.status === "loading" ? (
              <div className="flex flex-col gap-2" aria-label="加载中">
                {[0, 1].map((index) => (
                  <Skeleton key={index} className="h-9 w-full" />
                ))}
              </div>
            ) : state.status === "ready" && (data?.rules.length ?? 0) === 0 ? (
              <EmptyState
                compact
                title="还没有品类 YAML"
                description="推送规则来自品类 YAML 的 push 节;先到「源管理/配置编辑」建品类。"
              />
            ) : (
              (data?.rules ?? []).map((ruleFile) => (
                <RuleFileGroup
                  key={ruleFile.file}
                  ruleFile={ruleFile}
                  data={data}
                  currentTargets={(entry) => currentTargets(ruleFile.file, entry)}
                  onToggle={(entry, spec) => toggleTarget(ruleFile.file, entry, spec)}
                  saving={savingFile === ruleFile.file}
                  onSave={() => void saveRuleFile(ruleFile)}
                />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** 一个品类 YAML 的规则组:坏文件如实展示错误;targeting 条目出多选器。 */
function RuleFileGroup({
  ruleFile,
  data,
  currentTargets,
  onToggle,
  saving,
  onSave,
}: {
  ruleFile: PushRuleFile;
  data: ChannelsView | null;
  currentTargets: (entry: PushRuleEntry) => string[];
  onToggle: (entry: PushRuleEntry, spec: string) => void;
  saving: boolean;
  onSave: () => void;
}) {
  const targetingEntries = ruleFile.entries.filter((entry) => entry.platform !== null);
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border/60 p-3" data-testid={`rule-file-${ruleFile.category_id ?? ruleFile.file}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-2 text-sm">
          <span className="truncate font-medium text-foreground">
            {ruleFile.category_name ?? ruleFile.file}
          </span>
          {ruleFile.parse_ok ? null : <Badge variant="destructive">加载失败</Badge>}
          <span className="truncate font-mono text-[11px] text-muted-foreground">{ruleFile.file}</span>
        </p>
        {ruleFile.parse_ok && targetingEntries.length > 0 ? (
          <Button size="sm" disabled={saving} onClick={onSave}>
            {saving ? "保存中…" : "保存推送对象"}
          </Button>
        ) : null}
      </div>
      {ruleFile.parse_ok ? null : (
        <p className="text-xs text-destructive" role="alert">
          {ruleFile.error?.code}:{ruleFile.error?.message}(修好后此处自动恢复;去「配置编辑」处理)
        </p>
      )}
      {ruleFile.parse_ok && ruleFile.entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">该品类未配置 push 通道(条目仅入库)。</p>
      ) : null}
      {ruleFile.entries.map((entry) => {
        const bucket = entry.platform ? (data?.platforms[entry.platform] ?? []) : [];
        const selected = currentTargets(entry);
        return (
          <div
            key={`${ruleFile.file}:${entry.index}`}
            className="flex flex-col gap-1.5 rounded-md bg-muted/30 px-3 py-2"
            data-entry-index={entry.index}
          >
            <p className="flex flex-wrap items-center gap-1.5 text-xs">
              <Badge variant="default">{entry.channel}</Badge>
              {entry.has_template ? <Badge variant="outline">自定义模板</Badge> : null}
              {entry.route_count > 0 ? <Badge variant="outline">规则 ×{entry.route_count}</Badge> : null}
              {entry.platform === null ? (
                <span className="text-muted-foreground">该通道不支持目录寻址(对象由 target 引用决定)</span>
              ) : selected.length === 0 ? (
                <span className="text-warning">未选对象(该规则不会定向投递)</span>
              ) : (
                <span className="font-mono text-muted-foreground">{selected.join(", ")}</span>
              )}
            </p>
            {entry.platform ? (
              bucket.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">
                  {entry.platform} 目录为空:先在上区「刷新」(飞书)或等 bot 收到消息(telegram 被动积累)。
                </p>
              ) : (
                <div className="flex flex-col gap-1" role="group" aria-label={`推送对象 ${entry.channel}`}>
                  {bucket.map((option) => {
                    const spec = targetSpec(option);
                    const dead = data ? isDeadEntry(option, data.dead) : false;
                    return (
                      <label key={option.chat_id} className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={selected.includes(spec)}
                          onChange={() => onToggle(entry, spec)}
                          aria-label={`对象 ${option.name}`}
                        />
                        <span className="truncate">{option.name}</span>
                        <span className="truncate font-mono text-[11px] text-muted-foreground">
                          {option.chat_id}
                        </span>
                        {dead ? <Badge variant="destructive">死信</Badge> : null}
                      </label>
                    );
                  })}
                </div>
              )
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
