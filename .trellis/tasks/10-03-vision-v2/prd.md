# PRD:看图 v2 — 模型下载与 server 代管 + feed 图析详情 + 推送带图

## 背景

主人 2026-10-03 夜批「正式立档」(未来清单 A3)。vision-pipeline/detail-images 两轮交付时拍板推 v2 的总伞;本档先 PRD 立住盘子,design/implement 待主人点开工再补齐(参照既往 grill 流程)。

## Requirements(v2 骨架,待 grill 细化)

1. **模型下载**(原话承诺「后面可以下载」):应用内下载视觉模型到 `MYIA_HOME/models/`——mlx-vlm 生态(HF 源,断点续传,磁盘预检);设置屏「看图」分区加下载/删除/已装清单;vision.yaml 模型路径选择器
2. **server 代管**:MYIA 负责 mlx_vlm.server 生命周期——开机/按需自启、健康自检(/models)、死则自动降级只 OCR 并在 UI 提示;彻底解决「重启机器后 vl 失效」(当前 8080 是手工 nohup);打包发行版如何携带/引导安装 uv 与模型=设计要点
3. **feed 图析详情**:条目详情展开看全量 image_ocr(逐行置信度)+ image_caption 全文 + 图片本尊(若 v2 决定落图文件;当前产物纯文本不落图,落图与否待 grill——涉及隐私与体积)
4. **推送带图**:TG sendPhoto / 飞书 img 卡片(immediate 条目附原图或图析摘要卡);模板与 route 联动
5. **参考节(A4/A5)**:JS 渲染页取图随 v12-backlog 的 crawl4ai L3 实装自然获得,不在此档重复;三条 low 知悉项(SSRF 事后复核边界/DNS 失败标 ssrf 口径/HTML 先读后限)档案在 vision-pipeline 复查记录,若 v2 动到对应区域顺手收编

## Acceptance Criteria(拍板后细化)

- [x] 设置屏可一键下载/切换/删除视觉模型,下载失败可续传
- [x] 重启机器后首次 run:服务自动起或自动降级,UI 有状态提示,零手工
- [x] feed 详情展开显示逐行置信度 OCR 与 caption 全文
- [x] immediate 推送带图(或拍板后的替代形态);TG/飞书通道各实证一次

> **验收标注(2026-10-03 夜,终检落档;证据 `evidence/smoke/smoke-summary.json`
> + `evidence/install/install-summary.txt`)**:
> AC1 **passed**(真下载子句 = manual):sidecar 方法往返 S3-S6(list 含
> qwen3-vl-8b-mlx 真扫描 / active 拒删 / 非法 repo 结构化拒 / activate 往返
> + vision.yaml 逐字节复原)+ GUI G2/G3(设置屏模型管理卡真数据渲染、非法
> repo → role=alert 结构化报错零 job)+ 装机件四断言②③(.app 内嵌 UI 真数据);
> **真下载 ~16GB/断点续传不在冒烟范围 = manual**(非法 repo 结构化拒已实证,
> 磁盘预检/断点续传留真下载窗口,smoke-summary `sidecar-models-download-real`)。
> AC2 **passed**:冒烟中途原手工 nohup 进程死亡(连接拒绝)→
> `image.server.ensure` 自起 10.3s healthy(setsid 存活;`sidecar-ensure-autostart.txt`)
> ——即「零手工」的实证;persist E2E(`persist-e2e-*`)exit 0,P1-P6 全过。
> AC3 **passed**:GUI G1 + 装机④(feed 展开 = OCR 全文 + 53 行逐行置信度表
> + caption 全文;`gui-02-feed-expanded.png` / `v2-04-feed-expanded.png`)。
> AC4 **passed**(代码+测试面;真发实证留主人):组装层实做(base.py
> ItemImages / TG sendPhoto 先图后文 / 飞书 im/v1/images img 卡,失败降级
> 图析摘要卡不阻投递),`evidence/push/pytest-push-images.txt` 27 项新增 +
> 302 项回归全绿(httpx.MockTransport);digest 不带图两测试钉死。
> TG sendPhoto 真发 / 飞书开 im:resource 后真发 = mock 无法替代,待主人
> (push/notes.md「待主人实证」节)。

## 已拍板决议(自决,主人授权按建议执行;终检落档)

1. **persist(落图)缺省 false**:隐私/体积优先,品类 YAML 显式开
   `images.persist: true` 才落 `数据根/images/`(内容寻址,文件名 =
   sha256(content)[:16]);persist E2E 已验落盘与读回。
2. **模型分发 = HF `mlx-community` 直下(免 convert)**:repo 强制
   `mlx-community/<name>` 形态,snapshot_download + local_dir;断点续传
   天然(.incomplete 接续);磁盘预检(HfApi files_metadata 总量 ×
   shutil.disk_usage)不足即 `disk_insufficient` 拒下。dmg 附带精简模型
   不做(首跑下载路线)。
3. **ensure 快慢双路径 + 完成事件**:快路径(≤2s 快照)已健康即返
   `started:false`;慢路径后台线程自起 + 健康等待 ≤120s,应答立即返回,
   终态走 `image.server.completed` 事件——**原同步等 120s 会把单线程
   serve 循环全协议冻成队头阻塞的缺陷已修**(复查批)。
4. **落图回收 = `image.files.purge {days}`**:按 mtime 清超龄文件,
   CLI 面能力,零 UI。
5. **图片本尊显示与 purge UI = v2.2**:feed 详情当前展示 OCR/caption/
   逐行置信度文本面;图片本尊 `<img>` 渲染与设置屏 purge 按钮推迟 v2.2。
6. 排期决议:与 v1.1.2 桌面对齐批同窗口收口(装机/冒烟 2026-10-03 完成);
   优先序按推荐执行(代管 > 下载 > feed 详情 > 推送带图,四线全落)。

### 执行清单(无 implement.md,记于此;全数落地)

- [x] `src/myia/vision/models.py`:清单/下载(预检+断点)/删除/激活,
  huggingface-hub 惰性 import(extras `myia[vision]`)
- [x] `src/myia/vision/server.py`:status 探测 + ensure 代管(互斥锁/
  超窗杀孤儿/日志 >5MB 轮转)
- [x] `desktop/entry.py`:协议 v5 七方法(image.models.*×4 /
  image.server.*×2 / image.files.purge)+ 下载两事件 + server 完成事件
  + `_item_dict` 投影三键(image_caption/image_files/image_ocr_lines)
- [x] 复查修复批:ensure 队头阻塞(后台线程化)/ 孤儿回收 / 并发互斥 /
  半成品 incomplete 标记 / 同名守卫(model_exists 拒重下)/ 飞书卡片
  lark_md 转义 / vision-server 日志轮转 / feed 详情 UI remount
- [x] 桌面 UI:设置屏「看图模型管理」卡(清单/下载/删除/激活/服务徽章)
  + feed 图析详情展开(`vision-form.tsx` / `vision-api.ts` /
  `vision-models.test.tsx` / `feed-screen.tsx`)
- [x] 打包:`myia-core.spec` + `build-sidecar.sh` 收编 huggingface_hub
  (collect_all 两路等价);.app+dmg 构建装机(.app 132M,dmg 128M)
- [x] 测试:`tests/test_vision_models_server.py` / `tests/test_push_images.py`
  / sidecar 契约扩展 / vitest;冒烟+装机证据落 `evidence/`

## 待拍板(开工前)

1. 优先序(推荐:代管 > 下载 > feed 详情 > 推送带图);2. 图片文件是否落库(隐私/体积 vs 详情看图);3. 发行版模型分发策略(首跑下载 vs dmg 附带精简模型);4. 排期(建议 v1.2 池,与 Windows/crawl4ai 同窗口)

> 已决(见上「已拍板决议」节):2 = persist 缺省 false 显式开;3 = 首跑 HF
> 直下;1/4 = 按推荐执行,同窗口收口完成。
