# 可选：自动发章到中文网文平台

这条支线和写作完全解耦——写作只往 `novels/<书名>/chapters/` 里加文件，发布端自己记进度。所以"写了 138 章只发到 120 章"是正常状态，不要为了对齐去补发或回退。

`$SK` = 本 skill 的 `scripts/` 目录，`$A` = `assets/`。

**只有中国大陆平台需要这一步**，而且需要用户自己有平台账号和已过审的作品。先确认用户真的要发，再动手——发出去的章节删不回来。

## 额外依赖

写作只要 `openai`，发章要浏览器自动化：

```bash
pip install patchright loguru pyyaml
patchright install chromium
```

## 覆盖范围（诚实版）

| 平台 | 状态 |
|---|---|
| 番茄小说 | 跑得最熟，弹窗序列、article_id 捕获、失败重试都处理过 |
| 七猫小说 | 能跑，但走的是通用分支，比番茄脆弱 |
| 17K、塔读 | **不在本 skill 里**。原项目是各自一个一次性脚本（手改章节号再跑），没有打包进来 |

## 工作目录

`cookies/` 和 `state/` 生成在**当前工作目录**下（和 `novels/` 并排），不是 skill 目录里。要放别处就设 `WNU_HOME`。

## 第一次配一本书

1. **平台后台先建好作品并过审**，脚本只发章不建书。
2. 复制配置模板：

```bash
cp "$A/site-configs/fanqie.yaml.template" fanqie_我的书.yaml
```

3. 改三处：`novel_id`（作品后台 URL 里那串数字，**必填**）、`auth_check_url` 和 `chapter_list_url` 里的同一串数字和书名。书名带中文，整条 URL 从浏览器地址栏复制最稳。`new_chapter_url` 留空。
4. 登录（会开真实浏览器窗口，要用户扫码或收短信，不能静默完成——提前告诉用户）：

```bash
P=python3   # 装了 patchright 的那个解释器
$P $SK/wnu_cli.py webnovel login --account fanqie --site-config fanqie_我的书.yaml --headed
$P $SK/wnu_cli.py webnovel check --account fanqie --site-config fanqie_我的书.yaml
```

5. **第一次发一章，盯着看**：

```bash
C=novels/我的书/chapters
$P $SK/wnu_cli.py webnovel submit-next --account fanqie --novel-folder "$C" \
    --site-config fanqie_我的书.yaml --batch-size 1 --headed
```

确认线上那章标题、正文、章号都对，再放量。

## 日常发章

```bash
$P $SK/wnu_cli.py webnovel status --account fanqie --novel-folder "$C"
$P $SK/wnu_cli.py webnovel submit-next --account fanqie --novel-folder "$C" \
    --site-config fanqie_我的书.yaml --batch-size 2
```

守护进程（按北京日历天日更）：

```bash
$P $SK/wnu_cli.py webnovel daemon --account fanqie --novel-folder "$C" \
    --site-config fanqie_我的书.yaml --schedule daily --time-zone Asia/Shanghai \
    --daily-time 00:00 --batch-size 2
```

`--schedule interval --interval-hours N` 是另一种节奏。

**批次大小别贪**。一次发太多容易撞平台的频率限制和审核排队，日更 2 章是个稳妥的默认值。

## 进度怎么记的

状态文件是 `state/<account>_<书名>.json`（`--novel-folder` 指向 `.../<书名>/chapters` 时取父目录名）。`next_index` 是**排序后文件列表里的下标，不是章节号**——只有 `chapters/` 从 `001.txt` 连续开始时两者数值才相同。

用户已经手工发过前几章的话，先把 `next_index` 改成已发章数，否则会重发。

## 平台硬规则（排障时才需要读）

脚本里已经实现了这些，列在这里是为了手写一次性脚本或排查怪问题时有个依据。

### 番茄
- 编辑器是 ProseMirror。**只有剪贴板粘贴（Cmd+V）能触发 React 状态更新**，`insert_text` 和直接改 `innerHTML` 都无效，发布按钮会一直 disabled。
- 正确顺序：`page.evaluate` 写剪贴板 → **点一下编辑器把焦点抢回来**（evaluate 会偷焦点）→ `editor.press("Meta+a")` → `editor.press("Meta+v")`。要用 `editor.press()` 而不是 `page.keyboard.press()`，前者保证按键前元素已聚焦。
- 每章需要服务端真实创建的 `article_id`。`/publish/new` 和 `/publish/?enter_from=newchapter` 能加载界面但接口返回"文章ID为空"，按钮永远点不动。有效地址形如 `/publish/<article_id>?enter_from=newchapter`，要等重定向后再取，并用 `/publish/\d+` 校验（只匹配 `/publish/` 会放过坏地址）。
- 弹窗序列以 yaml 里的 `confirm_popups` 为准，平台会加弹窗（2026-05 就加过一步"内容检测方式"）。
- 提交后必须等 `.arco-modal` 变 hidden（30s 超时）才能关浏览器，`sleep(3)` 不够——确认请求可能还在飞。
- **弹窗没消失就不要推进 `next_index`**：重试一次"确认发布"，仍然不关就截图报错。手工重试时同理，宁可让 state 落后于现实。
- **发章失败后不要自己点"新建章节"**：失败内容已被自动存进当前槽位，再点可能覆盖已有章节的草稿。停下来问用户要正确的槽位 URL。

### 七猫
- 编辑器是 `div.q-contenteditable.book`，`keyboard.insert_text()` 可靠。
- 两个弹窗：① 版权确认"我已阅读并知晓"，**按钮文字里带倒计时**（如 `(5)`），要等倒计时走完再点；② 确认发布。
- 一定先处理版权弹窗再找确认发布，并且用 Playwright 的 `locator('a, button').filter(has_text=...)`——JS 的 `textContent.includes()` 会匹配到整个 modal 容器，JS `.click()` 还会命中尚未可见的隐藏按钮（要过滤 `offsetParent !== null`）。

### 通用判断
- **先 `check` 会话，再看 `status`，最后才动浏览器。** 大部分"发不了"其实是 cookie 过期。
- 不确定是否发成功时，**优先让 state 落后于现实**：重发的重复章可以在后台删，跳掉的章读者直接看到剧情断裂。
- 一次只处理一本书 + 一个平台，别顺手去看别的，容易把配置和状态搞混。
