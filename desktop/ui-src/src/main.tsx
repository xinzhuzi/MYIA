import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createHashRouter, RouterProvider } from "react-router-dom";

import App from "./App.tsx";
import "./index.css";

// 数据路由(createHashRouter):hash 语义不变(桌面 webview 免服务端回退,最稳),
// 但换来 useBlocker —— 配置编辑屏的 SPA 路由级 dirty 守卫依赖它(design §3
// 「切文件/路由离开时 dirty 守卫」的路由半边;App.tsx 的 <Routes> 子树原样保留)。
const router = createHashRouter([{ path: "*", element: <App /> }]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
