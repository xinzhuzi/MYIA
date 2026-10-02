import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/layout/app-layout";
import { DashboardScreen } from "@/screens/dashboard/dashboard-screen";
import { FeedScreen } from "@/screens/feed/feed-screen";
import { LogsScreen } from "@/screens/logs/logs-screen";
import { SettingsScreen } from "@/screens/settings/settings-screen";
import { SourcesScreen } from "@/screens/sources/sources-screen";

/**
 * 五屏路由(HashRouter:桌面 webview 下免服务端回退,最稳)。
 * 「/」= 仪表盘;未知路径一律回落仪表盘。
 *
 * 路由接的是 @/screens/* 五个真实实现(自带 health/doctor/run/logs 数据流,
 * 37 个 vitest 用例);此前接的 @/routes/* 是 C 阶段占位骨架,五屏真实实现
 * 在 v1.1 评审前从未进过打包产物(PRDAc#「五界面全部可用」在 ui/ 产物上
 * 不成立)—— 本文件即修复入口,骨架页仍留 @/routes/ 备查。
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardScreen />} />
        <Route path="feed" element={<FeedScreen />} />
        <Route path="sources" element={<SourcesScreen />} />
        <Route path="logs" element={<LogsScreen />} />
        <Route path="settings" element={<SettingsScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
