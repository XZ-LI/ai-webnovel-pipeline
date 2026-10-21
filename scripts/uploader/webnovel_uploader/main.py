from __future__ import annotations

import asyncio
import re as _re
from pathlib import Path

from loguru import logger
from patchright.async_api import async_playwright


BAD_SOURCE_MARKERS = (
    "抱歉，我不能",
    "抱歉，我无法",
    "抱歉，我不能执行",
    "不能为你提供超过",
    "以下是第",
)


def _read_validated_chapter(chapter_file: Path) -> str:
    text = chapter_file.read_text(encoding="utf-8").strip()
    compact = text.replace("\ufeff", "").strip()
    first_line = compact.splitlines()[0].strip() if compact else ""
    if not compact:
        raise ValueError(f"{chapter_file.name} is empty; refusing to upload.")
    if len(compact) < 1000:
        raise ValueError(f"{chapter_file.name} is too short ({len(compact)} chars); refusing to upload.")
    if any(marker in first_line for marker in BAD_SOURCE_MARKERS):
        raise ValueError(f"{chapter_file.name} starts with model refusal/metadata text; refusing to upload.")
    return compact


def _resolve_title(chapter_file: Path, title_from: str) -> str:
    if title_from == "filename":
        return chapter_file.stem
    lines = _read_validated_chapter(chapter_file).splitlines()
    title = lines[0].strip() if lines else chapter_file.stem
    title = _re.sub(r"^第\d+章\s*", "", title).strip()
    return title


def _resolve_content(chapter_file: Path, title_from: str) -> str:
    text = _read_validated_chapter(chapter_file)
    if title_from == "first_line":
        lines = text.splitlines()
        return "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return text


async def cookie_auth(account_file: str, site_config: dict) -> bool:
    auth_check_url = site_config["auth_check_url"]
    auth_check_text = site_config.get("auth_check_text", "")
    for attempt in range(3):
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(storage_state=account_file)
                    page = await context.new_page()
                    await page.goto(auth_check_url, wait_until="domcontentloaded", timeout=30000)
                    if auth_check_url not in page.url:
                        return False
                    if auth_check_text:
                        try:
                            await page.wait_for_selector(f"text={auth_check_text}", timeout=5000)
                        except Exception:
                            return False
                    return True
                except Exception:
                    return False
                finally:
                    await browser.close()
        except Exception as exc:
            if attempt < 2:
                logger.warning(f"Browser launch failed (attempt {attempt + 1}/3): {exc}. Retrying in 30s...")
                await asyncio.sleep(30)
            else:
                logger.error(f"Browser launch failed after 3 attempts: {exc}")
                return False
    return False


async def webnovel_login(account_file: str, site_config: dict, headless: bool = False) -> dict:
    login_url = site_config["login_url"]
    selectors = site_config.get("selectors", {})
    credentials = site_config.get("account", {})
    username = credentials.get("username", "")
    password = credentials.get("password", "")
    result: dict = {"success": False, "message": "", "account_file": account_file}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context()
        try:
            page = await context.new_page()
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            if username and password and selectors.get("username") and selectors.get("password"):
                await page.fill(selectors["username"], username)
                await page.fill(selectors["password"], password)
                if selectors.get("login_submit"):
                    await page.click(selectors["login_submit"])
            print("\n>>> Browser is open. Log in with your phone, then come back here and press Enter to save your session...")
            await asyncio.get_event_loop().run_in_executor(None, input, "")
            await asyncio.sleep(1)
            await context.storage_state(path=account_file)
            result["success"] = True
            result["message"] = "Login successful, session saved"
        except Exception as exc:
            result["message"] = str(exc)
        finally:
            await context.close()
            await browser.close()
    return result


async def webnovel_setup(account_file: str, site_config: dict, handle: bool = False, headless: bool = False) -> bool:
    if Path(account_file).exists() and await cookie_auth(account_file, site_config):
        return True
    if not handle:
        return False
    logger.info("Session invalid or missing — opening login flow...")
    result = await webnovel_login(account_file, site_config, headless=headless)
    return result["success"]


async def _fill_editor(page, selector: str, content: str, editor_type: str) -> None:
    import sys
    if editor_type == "quill":
        await page.evaluate(
            "(content) => { document.querySelector('.ql-editor').innerHTML = content; }",
            content,
        )
    elif editor_type == "tinymce":
        await page.evaluate(
            "(content) => { tinymce.get(0).setContent(content); }",
            content,
        )
    elif editor_type == "prosemirror":
        editor = page.locator(selector).first
        await editor.wait_for(state="visible", timeout=15000)
        modifier = "Meta" if sys.platform.startswith("darwin") else "Control"
        # Write clipboard content first (no focus needed for evaluate)
        await page.evaluate("async (text) => await navigator.clipboard.writeText(text)", content)
        await asyncio.sleep(0.3)
        # Click editor to give it focus, then immediately select-all + paste
        # Re-clicking after evaluate() restores focus lost during JS execution
        await editor.click()
        await asyncio.sleep(0.5)
        await editor.press(f"{modifier}+a")
        await asyncio.sleep(0.3)
        await editor.press(f"{modifier}+v")
        await asyncio.sleep(1)
    elif editor_type == "contenteditable":
        el = page.locator(selector).first
        await el.click()
        await el.fill(content)
    else:
        await page.fill(selector, content)


async def _click_new_chapter_and_wait(page, context, new_chapter_button: str):
    """
    Click 新建章节 and wait for the editor to open. Returns the page with the editor.
    The returned page's URL will contain a real article_id (numeric segment after /publish/).
    """
    import re as _re2
    _has_article_id = lambda url: bool(_re2.search(r'/publish/\d+', url))

    current_url = page.url
    pages_before = len(context.pages)

    await page.locator("button").filter(has_text=new_chapter_button).first.click(force=True)

    for _ in range(20):
        await asyncio.sleep(1)

        if len(context.pages) > pages_before:
            new_page = context.pages[-1]
            # Wait up to 10s for the new tab to redirect to the URL with article_id
            try:
                await new_page.wait_for_url(_re2.compile(r'/publish/\d+'), timeout=10000)
            except Exception:
                pass
            logger.info(f"New tab URL: {new_page.url}")
            return new_page

        if page.url != current_url and _has_article_id(page.url):
            logger.info(f"URL changed to editor: {page.url}")
            return page

        try:
            if await page.locator(".ProseMirror").first.is_visible():
                logger.info("ProseMirror appeared in-place")
                return page
        except Exception:
            pass

    return None


async def _capture_next_fanqie_editor(page, context, new_chapter_button: str) -> str | None:
    """Open the next Fanqie editor after a successful publish and return its real article URL."""
    import re as _re2

    before_pages = set(context.pages)

    # Success dialogs can offer "再写一章"; chapter management pages offer "新建章节".
    for text in ("再写一章", new_chapter_button):
        try:
            btn = page.locator("button, a").filter(has_text=text).first
            if await btn.count() > 0 and await btn.is_visible():
                logger.info(f"Clicking post-submit next-chapter action: {text!r}")
                await btn.click(force=True)
                break
        except Exception:
            pass
    else:
        # If no success action is visible, use the already-tested chapter-list path.
        logger.info("No post-submit next-chapter action visible; falling back to chapter list")
        return None

    for _ in range(20):
        await asyncio.sleep(1)
        for candidate in context.pages:
            if candidate not in before_pages and _re2.search(r"/publish/\d+", candidate.url):
                logger.info(f"Next chapter article URL captured from new tab: {candidate.url}")
                return candidate.url
        if _re2.search(r"/publish/\d+", page.url):
            logger.info(f"Next chapter article URL captured in current tab: {page.url}")
            return page.url
        try:
            for candidate in context.pages:
                if candidate not in before_pages:
                    await candidate.wait_for_url(_re2.compile(r"/publish/\d+"), timeout=1000)
                    logger.info(f"Next chapter article URL captured after redirect: {candidate.url}")
                    return candidate.url
        except Exception:
            pass

    return None


class WebNovelChapter:
    def __init__(
        self,
        chapter_file: Path,
        site_config: dict,
        account_file: str,
        headless: bool = True,
    ) -> None:
        self.chapter_file = chapter_file
        self.site_config = site_config
        self.account_file = account_file
        self.headless = headless
        self._next_chapter_url: str | None = None

        title_from = site_config.get("title_from", "first_line")
        self.title = _resolve_title(chapter_file, title_from)
        self.content = _resolve_content(chapter_file, title_from)

    async def _dismiss_popup(self, page):
        """Dismiss 我知道了 / feature announcement popup, and version-conflict dialog."""
        try:
            await page.wait_for_selector("text=我知道了", timeout=3000)
            logger.info("Popup visible — dismissing via Escape...")
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
            if await page.get_by_role("button", name="我知道了").is_visible():
                await page.get_by_role("button", name="我知道了").click()
            await asyncio.sleep(1)
            logger.info("Popup dismissed")
        except Exception:
            pass
        # Handle "版本冲突提示" — keep local draft so our content paste works
        try:
            conflict_btn = page.locator("button").filter(has_text="继续编辑本地").first
            if await conflict_btn.count() > 0 and await conflict_btn.is_visible():
                logger.info("Version conflict dialog detected — clicking '继续编辑本地'")
                await conflict_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

    async def _open_editor(self, page, context, chapter_list_url: str, new_chapter_button: str):
        """Open a FRESH chapter editor for a new chapter (not an existing chapter's slot). Returns the editor page."""
        # 1. If a specific fresh URL is configured, use it directly
        new_chapter_url = self.site_config.get("new_chapter_url", "")
        if new_chapter_url:
            logger.info(f"Navigating to configured editor URL: {new_chapter_url}")
            await page.goto(new_chapter_url, wait_until="domcontentloaded", timeout=60000)
            await self._dismiss_popup(page)
            await page.locator(".ProseMirror").first.wait_for(state="visible", timeout=30000)
            logger.info(f"Editor ready at: {page.url}")
            return page

        # 2. Navigate to chapter list and click 新建章节
        logger.info(f"Navigating to chapter list: {chapter_list_url}")
        await page.goto(chapter_list_url, wait_until="commit", timeout=60000)
        await page.wait_for_load_state("domcontentloaded", timeout=60000)

        if "fanqienovel.com/main/writer" not in page.url:
            raise RuntimeError("Session expired — run `wnu webnovel login` again")

        await page.wait_for_function(
            f"Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim() === '{new_chapter_button}')",
            timeout=15000,
        )

        await self._dismiss_popup(page)

        # Click 新建章节 and detect the result
        logger.info(f"Clicking '{new_chapter_button}'...")
        editor_page = await _click_new_chapter_and_wait(page, context, new_chapter_button)

        if not editor_page:
            await page.screenshot(path="/tmp/fanqie_newchapter_fail.png")
            logger.error("新建章节 did not open editor. Screenshot: /tmp/fanqie_newchapter_fail.png")
            raise RuntimeError(
                "新建章节 button did not navigate to an editor. "
                "Please open fanqienovel.com in your browser, click 新建章节, "
                "copy the URL, and set it as new_chapter_url in fanqie.yaml."
            )

        await self._dismiss_popup(editor_page)
        await editor_page.locator(".ProseMirror").first.wait_for(state="visible", timeout=30000)
        logger.info(f"Editor ready at: {editor_page.url}")
        return editor_page

    async def submit(self) -> str | None:
        """Submit the chapter. Returns the next chapter slot URL if discovered post-submission."""
        cfg = self.site_config
        selectors = cfg.get("selectors", {})
        chapter_list_url = cfg["chapter_list_url"]
        new_chapter_button = cfg.get("new_chapter_button", "新建章节")
        editor_type = cfg.get("content_editor_type", "textarea")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(storage_state=self.account_file)
            await context.grant_permissions(["clipboard-read", "clipboard-write"])
            try:
                page = await context.new_page()
                page.on("console", lambda msg: logger.debug(f"BROWSER [{msg.type}]: {msg.text}") if msg.type == "error" else None)
                page.on("pageerror", lambda err: logger.error(f"PAGE JS ERROR: {err}"))

                edit_article_data: dict = {}

                async def _capture_edit_article(response):
                    if "edit_article" in response.url or "draft" in response.url:
                        try:
                            body = await response.json()
                            edit_article_data.update(body)
                            logger.info(f"[DEBUG] edit_article response: {str(body)[:400]}")
                        except Exception:
                            pass

                context.on("response", _capture_edit_article)

                page = await self._open_editor(page, context, chapter_list_url, new_chapter_button)
                await asyncio.sleep(2)

                # If edit_article returned a chapter ID, navigate to that URL
                _api_data = edit_article_data.get("data") or {}
                article_id = (
                    _api_data.get("item_id")
                    or _api_data.get("article_id")
                    or _api_data.get("chapter_id")
                )
                if article_id:
                    novel_id = cfg.get("novel_id")
                    if not novel_id:
                        raise RuntimeError(
                            "site config 缺少 novel_id —— 它是你作品后台 URL 里那串数字。"
                            "没有它无法拼出本章编辑器地址。"
                        )
                    new_url = f"https://fanqienovel.com/main/writer/{novel_id}/publish/{article_id}"
                    logger.info(f"[DEBUG] Navigating to article URL: {new_url}")
                    await page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
                    await page.locator(".ProseMirror").first.wait_for(state="visible", timeout=15000)

                if selectors.get("chapter_content"):
                    logger.info(f"Content: {len(self.content)} chars")
                    await _fill_editor(page, selectors["chapter_content"], self.content, editor_type)
                    await asyncio.sleep(1)

                if selectors.get("chapter_number"):
                    num = str(int(self.chapter_file.stem))
                    logger.info(f"Chapter number: {num}")
                    await page.fill(selectors["chapter_number"], num)

                if selectors.get("chapter_title"):
                    logger.info(f"Title: {self.title!r}")
                    await page.fill(selectors["chapter_title"], self.title)

                # Wait for publish button to become enabled (React must recognize the pasted content)
                btn_disabled = True
                if selectors.get("chapter_submit"):
                    for _wait in range(20):
                        btn_disabled = await page.evaluate(
                            "() => document.querySelector('.publish-button')?.disabled ?? true"
                        )
                        if not btn_disabled:
                            break
                        await asyncio.sleep(1)

                # Debug: read back form state
                title_val = await page.evaluate(
                    "() => document.querySelector('input.serial-editor-input-hint-area')?.value || ''"
                )
                editor_text = await page.evaluate(
                    "() => (document.querySelector('.ProseMirror')?.innerText || '').trim().slice(0, 80)"
                )
                logger.info(f"[DEBUG] title_val={title_val!r} editor_preview={editor_text!r} btn_disabled={btn_disabled}")
                await page.screenshot(path="/tmp/fanqie_before_submit.png")

                if selectors.get("chapter_submit"):
                    if not btn_disabled:
                        logger.info("Button enabled — clicking...")
                        await page.click(selectors["chapter_submit"])
                    else:
                        logger.error("Button still disabled after 20s — aborting chapter submission")
                        raise RuntimeError(
                            f"Publish button remained disabled for {self.chapter_file.name}. "
                            "Content may not have been recognized by the editor."
                        )
                    await asyncio.sleep(1)

                popups = cfg.get("confirm_popups", [])
                for i, popup in enumerate(popups):
                    action = popup.get("action", "click")
                    wait_for = popup.get("wait_for", "")
                    timeout = popup.get("timeout", 15000)

                    if wait_for:
                        logger.info(f"Popup {i + 1}: waiting for '{wait_for}'...")
                        try:
                            await page.wait_for_selector(wait_for, state="visible", timeout=timeout)
                        except Exception as e:
                            logger.warning(f"Popup {i + 1}: '{wait_for}' not found, proceeding")
                    else:
                        await asyncio.sleep(popup.get("delay", 4))

                    await page.screenshot(path=f"/tmp/fanqie_popup_{i+1}.png")

                    if action == "click":
                        btn_text = popup["button"]
                        clicked = await page.evaluate(f"""() => {{
                            const btn = Array.from(document.querySelectorAll('button'))
                                .find(b => b.textContent.trim() === '{btn_text}');
                            if (btn) {{ btn.click(); return true; }}
                            return false;
                        }}""")
                        logger.info(f"Clicked '{btn_text}': {clicked}")

                    elif action == "click_if_present":
                        btn_text = popup["button"]
                        btn = page.locator("button").filter(has_text=btn_text).first
                        if await btn.count() > 0 and await btn.is_visible():
                            await btn.click()
                            logger.info(f"click_if_present: clicked '{btn_text}'")
                            await asyncio.sleep(1)
                        else:
                            logger.info(f"click_if_present: '{btn_text}' not present, skipping")

                    elif action == "radio_then_click":
                        radio_text = popup["radio"]
                        btn_text = popup["button"]
                        radio = page.locator(".arco-radio").filter(has_text=radio_text).first
                        if await radio.count() > 0 and await radio.is_visible():
                            await radio.click()
                            logger.info(f"Selected '{radio_text}' via playwright locator")
                        else:
                            logger.warning(f"Radio '{radio_text}' not visible; clicking '{btn_text}' directly")
                        # Wait for React state to settle after radio selection before clicking confirm
                        await asyncio.sleep(2)
                        # Find confirm button — retry up to 3x with 1s gap if not yet visible
                        confirm_btn = None
                        for _try in range(3):
                            _btn = page.locator(".arco-modal-footer button").filter(has_text=btn_text).first
                            if await _btn.count() == 0 or not await _btn.is_visible():
                                _btn = page.locator("button").filter(has_text=btn_text).first
                            if await _btn.count() > 0 and await _btn.is_visible():
                                confirm_btn = _btn
                                break
                            logger.warning(f"'{btn_text}' not yet visible (try {_try + 1}/3), waiting 1s...")
                            await asyncio.sleep(1)
                        if confirm_btn is not None:
                            await confirm_btn.scroll_into_view_if_needed()
                            await confirm_btn.click()
                            logger.info(f"Clicked '{btn_text}' via playwright locator")
                            await asyncio.sleep(1)
                            await page.screenshot(path=f"/tmp/fanqie_after_confirm_{i+1}.png")
                            modal_html = await page.locator(".arco-modal").first.inner_html() if await page.locator(".arco-modal").count() > 0 else "NO MODAL"
                            logger.info(f"After confirm click — modal HTML (first 400): {modal_html[:400]}")
                        else:
                            clicked = await page.evaluate(f"""() => {{
                                const btn = Array.from(document.querySelectorAll('button'))
                                    .find(b => b.textContent.trim() === '{btn_text}');
                                if (btn) {{ btn.click(); return true; }}
                                return false;
                            }}""")
                            logger.info(f"Clicked '{btn_text}' via JS fallback: {clicked}")

                    await asyncio.sleep(1)

                # Wait for the confirmation modal to close — this means the server accepted the request.
                # A bare sleep(3) is not enough; the request can still be in flight when the browser closes.
                try:
                    await page.wait_for_selector(".arco-modal", state="hidden", timeout=60000)
                    logger.info("Modal closed — submission confirmed by server")
                except Exception:
                    # Fanqie sometimes ignores the first confirm click. Retry the final confirm before failing.
                    try:
                        confirm_btn = page.locator(".arco-modal-footer button").filter(has_text="确认发布").first
                        if await confirm_btn.count() == 0 or not await confirm_btn.is_visible():
                            confirm_btn = page.locator("button").filter(has_text="确认发布").first
                        if await confirm_btn.count() > 0 and await confirm_btn.is_visible():
                            logger.warning("Modal still visible; retrying 确认发布 once")
                            await confirm_btn.scroll_into_view_if_needed()
                            await confirm_btn.click()
                            await page.wait_for_selector(".arco-modal", state="hidden", timeout=60000)
                            logger.info("Modal closed after retry — submission confirmed by server")
                        else:
                            raise RuntimeError("confirmation modal remained visible")
                    except Exception as exc:
                        await page.screenshot(path="/tmp/fanqie_modal_still_visible.png")
                        raise RuntimeError(
                            f"Fanqie confirmation modal did not close for {self.chapter_file.name}; "
                            "submission was not confirmed and state should not advance."
                        ) from exc
                await asyncio.sleep(1)
                await page.screenshot(path="/tmp/fanqie_final.png")
                logger.info(f"Submitted: {self.chapter_file.name}")

                # After submission, capture the next real article URL automatically.
                next_url: str | None = None
                try:
                    next_url = await _capture_next_fanqie_editor(page, context, new_chapter_button)
                    if not next_url and ("chapter-manage" in page.url or ("writer" in page.url and "publish" not in page.url)):
                        logger.info("Attempting post-submission 新建章节 to get next slot URL...")
                        next_page = await _click_new_chapter_and_wait(page, context, new_chapter_button)
                        if next_page and _re.search(r'/publish/\d+', next_page.url):
                            next_url = next_page.url
                            logger.info(f"Next chapter slot URL captured: {next_url}")
                except Exception as e:
                    logger.info(f"Post-submission next article-id capture failed: {e}")

                self._next_chapter_url = next_url
                return next_url
            finally:
                await context.close()
                await browser.close()
