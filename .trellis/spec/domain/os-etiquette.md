# 系统交互礼仪(静默操作铁律,2026-10-03 主人指示)

> 主人在前台干活,自动化操作严禁抢焦点。适用于一切代理/工作流/主会话命令。

## 工作区共享礼仪(多会话并行,10-03-shared-build-races 定案)

6. **清场禁全目录还原**:严禁 `git checkout -- <目录>/` 式清场(会吞掉并行会话在途改动,实锤:yaml-editor 冒烟 diff 被并行 e2e 还原);只还原**自己创建/修改的显式文件清单**
7. **共享构建缓存隔离**:任何调用 PyInstaller 的脚本必须先导出检出内私有 `PYINSTALLER_CONFIG_DIR`(build-sidecar.sh 已内置);新脚本照抄该行

## 规则

1. **禁前台切换**:严禁 `open -a <App>`(默认激活)、`osascript ... activate`、任何置前/切换窗口的命令
2. **需要拉起 App 时**:`open -g -a <App>`(不夺焦)或 `open -a <App> --hide`(启动即藏)
3. **验证优先无 UI**:进程用 `pgrep`/`ps`;功能用二进制直跑(如 `MYIA.app/Contents/MacOS/myia serve` + JSON-RPC 探测);数据用文件/sqlite 实查
4. **截图只许窗口级且仅目标 App**(全屏截图会截到主人私人内容——2026-10-03 冒烟实测踩过,已弃删);拿不到就如实写「需主人自验」,不得为取证抢屏
5. 装机/覆盖安装后**不自动拉起**让主人看;报告路径与命令,主人自己点开
6. **全壳冒烟必须独立锁域**:凡启动整个壳(世事/MYIA,非仅 sidecar)冒烟,必须 `MYIA_HOME=<沙箱目录>` 走独立数据根=独立 `.instance.lock`,禁止裸拉。锁根与数据根同源:壳进程读 `MYIA_HOME`,未设即落平台真根 `~/Library/Application Support/MYIA`(dev 构建经壳跑同样被注入真根,python spec 的「dev 回退 cwd」只适用于不经壳直跑 entry.py)——dev 与 release 装机包默认同根=同锁域:第二实例抢不到锁秒退,还会 `open -b com.myia.app` 让持锁实例亮窗,对前台主人即抢屏(壳内注释「既有实例经 Reopen 亮窗,不夺屏不弹窗」是主人双击视角;同一机制,冒烟误触发视角=抢主人屏——2026-10-03 实测踩过)。拉起前 `pgrep -x MYIA` 可作预检(进程名大小写敏感,`pgrep myia` 漏检;`-f` 会被路径中的 MYIA 误命中),但预检不免沙箱:主人没在跑,裸拉仍读写主人真实数据根;冒烟持共享锁期间,主人自己启动反被挡+被 open -b 弹到冒烟实例
7. **env 必须真送达壳内**:shell 前缀 `MYIA_HOME=x open -a …` 对 open 启动的 GUI App 无效(open 不透传 shell env,main.rs 注释原话)——静默不沙箱,锁与数据仍落真根。有效通道三选一:①dev:`MYIA_HOME=<沙箱> npm run tauri dev`(desktop/ 下,CLI 进程链继承 env);②release 直跑二进制:`MYIA_HOME=<沙箱> /Applications/世事.app/Contents/MacOS/MYIA`;③`open --env MYIA_HOME=<沙箱> -g -a 世事` 或 `launchctl setenv MYIA_HOME <沙箱>`(launchctl 的 env 对 open 启动的 GUI App 可见;选它收尾必按第 8 条清场——`MYIA_HOME` 残留会劫持主人下次启动进沙箱根:壳尊重已设 env 不夺权)
8. **launchctl setenv 必清场**:冒烟凡经 `launchctl setenv` 注入的变量(含第 7 条通道③的 `MYIA_HOME`),收尾必须逐个 `launchctl unsetenv <VAR>` 清掉;这是收尾必做项不是可选项——自列冒烟步骤时,unsetenv 必须单独列为收尾一步(`MYIA_SMOKE_ROUTE`——冒烟用临时导航钩子,产品代码已移除——残留曾劫持主人世事此后每次启动跳 #/settings,2026-10-03 实测踩过,当日手动 unsetenv 修复)

> 本文件是所有工作流 COMMON 的强制引用条目;分发模板(.trellis/spec/guides/ai-dispatch-template.md)已同步。

> 桌面壳自身同受此律:10-03-quiet-launch 起 MYIA 静默启动——主窗口出厂隐藏,点 Dock/
> 对运行中实例再 open 才亮出,冷启动后自动 deactivate 让回前台;dev 构建与
> `MYIA_SHOW_ON_START=1` 例外照常显示(验证逃生口)。
