# 世事品牌图标

**本目录是图标唯一事实源**(`src-tauri/icons/` 是 `tauri icon` 生成物,随时可由
本目录再生成;`ui-src/public/myssia-icon.svg` 是随 UI 分发的原样拷贝)。

定名(主人 2026-10-04 终版):项目唯一正式名 **myssia**(中文名 **世事**,README
标题写「myssia(中文名:世事)」);tauri 内部工件本轮已统一——`com.myssia.app` /
`myssia-core` / `myssia-core.spec` / 资产别名 `myssia_*`;仅运行时数据身份保留旧名
(`MYIA_HOME` 环境变量、数据根 `~/Library/Application Support/MYIA`、`~/.myia`、
keychain 名空间 `myia/<scope>/<name>`、`mainBinaryName=MYIA`),避免既有装机的数据根
与钥匙链凭据迁移。myia/shishi 皆为历史名。

意象:**眼睛背后是一个宇宙**——眼是"替主人看着世事"的经典情报意象(2026-10-02
立项时定下),眼底即宇宙:双对数旋臂星系 + 四团星云 + 星场/星芒;瞳孔为黑洞芯
(光子环 + 吸积弧 + 瞳中一粒星);保留青(#22D3EE)→紫(#8B5CF6)渐变品牌色与
右上信号触点(承前作 radar 意象)。色系:Linear 式深空蓝底 #05070F→#0B1226。

实现约束(沿 2026-10-02 立的规矩):零 SVG 滤镜,纯渐变/形状,浏览器与 rsvg
渲染一致;坐标经 `/tmp` 一次性 Python 脚本实算(对数螺旋 `r=a·e^{bθ}`,
a=112/b=0.30/倾角 -0.32,种子 42),生成脚本即弃,改图直接改本 SVG。

再生成:`rsvg-convert -w 1024 -h 1024 -o myssia-icon-1024.png myssia-icon.svg`
全尺寸图标集:`cd desktop && npx tauri icon branding/myssia-icon-1024.png`
