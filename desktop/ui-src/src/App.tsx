import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/layout/app-layout";
import { DashboardScreen } from "@/screens/dashboard/dashboard-screen";
import { FeedScreen } from "@/screens/feed/feed-screen";
import { ImageScreen } from "@/screens/image/image-screen";
import { LogsScreen } from "@/screens/logs/logs-screen";
import { MessagingScreen } from "@/screens/messaging/messaging-screen";
import { SettingsScreen } from "@/screens/settings/settings-screen";
import { SourcesScreen } from "@/screens/sources/sources-screen";
import { YamlEditorScreen } from "@/screens/yaml-editor/yaml-editor-screen";

/**
 * 八屏路由(HashRouter:桌面 webview 下免服务端回退,最稳)。
 * 「/」= 仪表盘;未知路径一律回落仪表盘。
 *
 * 路由接的是 @/screens/* 真实实现(自带 health/doctor/run/logs 数据流);
 * 此前接的 @/routes/* 是 C 阶段占位骨架,五屏真实实现在 v1.1 评审前从未
 * 进过打包产物(PRDAc#「五界面全部可用」在 ui/ 产物上不成立)—— 本文件
 * 即修复入口,骨架页仍留 @/routes/ 备查。
 *
 * /image = 看图(10-03-image-input):图片即输入的情报分析工具屏,
 * 位次=情报流之后(与侧栏一致)。
 * /yaml-editor = 配置编辑(10-03-yaml-editor):品类 YAML 原文编辑屏,
 * 源管理行「编辑」带 ?file= 预选(位次=源管理之后,与侧栏一致)。
 * /messaging = 消息(10-03-messaging-ui):通道目录 + 推送规则挑对象,
 * 位次=配置编辑之后(与侧栏一致);命名直白用「消息」,不用行话。
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardScreen />} />
        <Route path="feed" element={<FeedScreen />} />
        <Route path="image" element={<ImageScreen />} />
        <Route path="sources" element={<SourcesScreen />} />
        <Route path="yaml-editor" element={<YamlEditorScreen />} />
        <Route path="messaging" element={<MessagingScreen />} />
        <Route path="logs" element={<LogsScreen />} />
        <Route path="settings" element={<SettingsScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
