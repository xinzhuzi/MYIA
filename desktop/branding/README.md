# 世事品牌图标

**本目录是图标唯一事实源**(`src-tauri/icons/` 是 `tauri icon` 生成物,随时可由
本目录再生成;`ui-src/public/shishi-icon.svg` 是随 UI 分发的原样拷贝)。

定名(主人 2026-10-03):产品名 **世事**(原 MYIA,内部标识符 com.myia.app /
MYIA_HOME / myia-core / mainBinaryName=MYIA 不变,避免数据根与发布管道迁移)。

意象:**眼睛背后是一个宇宙**——眼是"替主人看着世事"的经典情报意象(2026-10-02
立项时定下),眼底即宇宙:双对数旋臂星系 + 四团星云 + 星场/星芒;瞳孔为黑洞芯
(光子环 + 吸积弧 + 瞳中一粒星);保留青(#22D3EE)→紫(#8B5CF6)渐变品牌色与
右上信号触点(承前作 radar 意象)。色系:Linear 式深空蓝底 #05070F→#0B1226。

实现约束(沿 2026-10-02 立的规矩):零 SVG 滤镜,纯渐变/形状,浏览器与 rsvg
渲染一致;坐标经 `/tmp` 一次性 Python 脚本实算(对数螺旋 `r=a·e^{bθ}`,
a=112/b=0.30/倾角 -0.32,种子 42),生成脚本即弃,改图直接改本 SVG。

再生成:`rsvg-convert -w 1024 -h 1024 -o shishi-icon-1024.png shishi-icon.svg`
全尺寸图标集:`cd desktop && npx tauri icon branding/shishi-icon-1024.png`
