# 推送带图(immediate 组装层)— 实现注记 2026-10-03

测试证据:`pytest-push-images.txt`(新增 `tests/test_push_images.py` 27 项 + push/feishu/messaging 回归 302 项,全绿;全 httpx.MockTransport,零外网)。

## 改动面(纯追加,diff +291/-0)

- `src/myia/push/base.py`:`clip_text` / `ItemImages` / `item_images`(消费 collect 落的 `metadata.image_files`(本地绝对路径 list)与 `image_caption`;非 list/非 str 剔除,缺失文件不进 `paths` 但计入 `declared`;`image_files` 缺席 → None → 通道零行为变化)
- `src/myia/push/telegram.py`:`CAPTION_LIMIT=1024`、`build_photo_caption`(标题全文优先、图析摘要截断、尾省略号计入上限)、`TelegramChannel.send_photo(chat_id, photo_path, caption)`(multipart sendPhoto,错误口径与 sendMessage 对齐:http_error / invalid_response / telegram_api_error / photo_read_error)、`_send_item_photo`(仅 `kind=immediate` 且单条目;先图后文;文件缺失/发送失败告警回退纯文本,不阻推送)
- `src/myia/push/feishu_card.py`:`IMAGES_API_URL`(`im/v1/images`)、`_upload_image`(multipart `image_type=message` + 图片文件 → `data.image_key`)、`_attach_card_image`(上传成功 → 条目 div 后插 `img` 元素 `img_key`+alt=图析摘要截断;失败/文件缺失 → 降级「图析摘要卡」文本形态:lark_md `　└ 图析: …` 摘要行 + `　└ [配图 N 张未附]` 注记)

## 拍板与理由

- **TG 形态选「先 sendPhoto 再 sendMessage」**(任务给的两选一):正文走原 4096 分段路径逐字节不变,图失败永不影响正文;caption=标题+图析摘要(纯文本,不声明 parse_mode),超限只压摘要。模板渲染失败时零请求发出(compose 先于图),不产孤儿图。
- **digest 不带图**:`kind != "immediate"` 或多条目直接原路径(digest 批量、route/when 逻辑零改动,仅组装层增强;`test_telegram_digest_with_images_never_sends_photo` / `test_feishu_digest_with_images_card_is_byte_identical` 钉死)。
- **immediate 单条目假设**:digest.py `send_immediate` 与 delivery 定向路径均逐条目调用 `channel.send([item], …)`(grep 核:`digest.py:74`、`delivery.py:335`),通道内 `len(items)==1` 守卫只是防御,非依赖。

## 飞书 image_key 路径注记(如实,不硬造)

- image_key 路径**已实做**(非后续项):`im/v1/images` 与发消息同一 open.feishu.cn 域、同一 tenant access token(Bearer)、同一可注入 httpx client——adapter 能力内,故按任务「支持就实做」分支落地。
- **部署前置**:飞书开放平台应用需开 `im:resource`(获取及上传图片或文件资源)权限;未开通时上传返回非零 code(如 99991672),通道自动降级图析摘要卡并告警,不阻投递(测试 `test_feishu_upload_failure_degrades_to_caption_summary_card` 覆盖)。**主人实证时若想走 img 元素,先到开放平台后台开该权限**。
- 卡片 `img` 元素形态:`{"tag":"img","img_key":…,"alt":{"tag":"plain_text",…}}`;alt 无摘要时退回条目标题。

## 待主人实证(mock 无法替代)

1. TG:`sendPhoto` 真发一张(私聊数字 chat_id);>10MB 图 Bot API 会拒(未做客户端预检,靠回退)。
2. 飞书:开 `im:resource` 后真发 img 卡;未开权限时确认降级卡到达。
3. 与并行代理的 collect 侧对齐:persist 品类条目 metadata 实际带 `image_files`(绝对路径)后端到端一次。
