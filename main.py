import base64
import ctypes
import hashlib
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import ddddocr
import requests
from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, QThread, QTimer, Signal, Qt,
)
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QTextCursor, QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGraphicsOpacityEffect,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QMessageBox, QGraphicsDropShadowEffect,
)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, NoSuchWindowException,
)
from exam_parsers import (
    TaskPageState,
    detect_comprehensive_question_type,
    get_comprehensive_required_score,
    is_comprehensive_score_accepted,
    is_unauthorized_status,
    parse_task_page_html,
)
from settings_store import load_settings, save_settings

BASE_URL = "https://dxpx.uestc.edu.cn"
TASK_PAGE_URL = f"{BASE_URL}/user/task"
COMPREHENSIVE_EXAM_PATH = "/jjfz/exam_center/end_exam"
DEFAULT_COURSE_TOTAL = 11
DEFAULT_VIDEO_TOTAL = 10

COLOURS = {
    "accent1": "#FF4D32",
    "accent2": "#F23E23",
    "accent3": "#C10000",
    "accent4": "#910000",
    "accent5": "#610000",
    "accent6": "#3D0000",
    "dk1": "#FFB897",
    "lt1": "#FF9E7E",
    "dk2": "#FF8365",
    "lt2": "#FF694C",
    "bg": "#1C0A08",
    "surface": "#2A100E",
    "text": "#FFD5C8",
    "text2": "#FFB897",
}

CSS_STUDY_BUTTON = "div.lesson_center_a a.study"
CSS_GOOD_COURSE_1 = "body > div > div.w1150 > div.wrap_left > div.wrap_left_list.lesson_left > ul > li:nth-child(1) > div > a > span"
CSS_GOOD_COURSE_2 = "body > div > div.w1150 > div.wrap_left > div.wrap_left_list.lesson_left > ul > li:nth-child(2) > div > a > span"
CSS_REQUIRED_BTN = "body > div > div.w1150 > div.wrap_right > div.lesson1_cont.q_lesson1_cont > div.lesson1_title > div > a:nth-child(2)"
CSS_REQUIRED_LIST = "div.l_list_right > h2 > a"
CSS_PUBLIC_SUBMIT = "a.public_submit"
CSS_PUBLIC_CANCEL = "a.public_cancel"
CSS_VIDEO_PLAY_BTN = "#wrapper > div > div.plyr__controls > button:nth-child(1)"
CSS_VIDEO_TIME = 'div[aria-label="Current time"]'
CSS_VIDEO_END_FLAG = "#wrapper > div > div.plyr__controls > div.plyr__controls__item.plyr__menu > button > span"
CSS_SIDEBAR_LINKS = "a[style]"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"

PAGE_LOAD_RETRIES = 3
PAGE_LOAD_SLEEP = 2


class WatcherWorker(QThread):
    log_msg = Signal(str)
    course_start = Signal(int, int, str)
    course_done = Signal(int)
    video_info = Signal(int, int)
    video_time = Signal(str, int)
    status_update = Signal(str)
    exam_score = Signal(int)
    exam_progress_info = Signal(int, int)
    question_progress = Signal(int, int)
    phase_update = Signal(str)
    finished = Signal()
    login_failed = Signal(str)

    def __init__(self, username: str, password: str, deepseek_key: str):
        super().__init__()
        self.username = username
        self.password = password
        self.deepseek_key = deepseek_key
        self._stop = False

    def stop(self):
        self._stop = True

    def log(self, msg: str):
        self.log_msg.emit(msg)

    def run(self):
        try:
            self._run_automation()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log(f"[ERROR] {e}")
        finally:
            self.finished.emit()

    def _remove_blank(self, driver):
        driver.execute_script(
            "var items = document.getElementsByTagName('a');"
            "for (var i = 0; i < items.length; i++) { items[i].target='_self'; }"
        )

    def _safe_get(self, driver, url: str, retries: int = PAGE_LOAD_RETRIES) -> bool:
        for attempt in range(1, retries + 1):
            try:
                driver.get(url)
                time.sleep(PAGE_LOAD_SLEEP)
                return True
            except (TimeoutException, WebDriverException) as e:
                if attempt < retries:
                    self.log(f"  页面加载异常，正在重试 ({attempt}/{retries})...")
                    time.sleep(3)
                else:
                    self.log(f"  页面加载失败: {e}")
                    try:
                        driver.refresh()
                        time.sleep(PAGE_LOAD_SLEEP)
                        return True
                    except Exception:
                        return False
        return True

    def _close_extra_windows(self, driver, main_handle):
        try:
            for h in driver.window_handles:
                if h != main_handle:
                    driver.switch_to.window(h)
                    driver.close()
            driver.switch_to.window(main_handle)
        except NoSuchWindowException:
            pass

    def _ensure_page_ok(self, driver):
        try:
            ready = driver.execute_script("return document.readyState")
            if ready != "complete":
                time.sleep(3)
        except Exception:
            time.sleep(3)

    def _mute_video(self, driver):
        try:
            driver.execute_script("""
                var v = document.getElementById('video');
                if (v) { v.muted = true; }
                if (typeof player != 'undefined' && player) { player.muted = true; }
            """)
        except Exception:
            pass

    def _address_dialogs(self, driver):
        try:
            cancel = driver.find_elements(By.CSS_SELECTOR, CSS_PUBLIC_CANCEL)
            if cancel and cancel[0].is_displayed():
                cancel[0].send_keys(Keys.ENTER)
                time.sleep(0.5)
                return
        except Exception:
            pass
        try:
            btn = driver.find_elements(By.CSS_SELECTOR, CSS_PUBLIC_SUBMIT)
            if btn and btn[0].is_displayed():
                btn[0].click()
                time.sleep(0.5)
        except Exception:
            pass

    def _clear_overlays(self, driver):
        driver.execute_script("""
            var shade = document.querySelector('.layui-layer-shade');
            var loading = document.querySelector('.layui-layer-loading');
            if (shade) shade.remove();
            if (loading) loading.remove();
            if (typeof layer != 'undefined') layer.closeAll('loading');
        """)

    def _watch_video(self, driver):
        driver.implicitly_wait(0)
        last_time = ""
        stuck_count = 0
        try:
            try:
                total_dur = driver.execute_script(
                    "var v=document.getElementById('video');return v?v.duration||0:0")
            except Exception:
                total_dur = 0
            total_dur = int(total_dur)
            ticks = 0
            while not self._stop:
                self._clear_overlays(driver)
                self._address_dialogs(driver)

                try:
                    ended = driver.execute_script(
                        "var v=document.getElementById('video');return v?v.ended:true")
                    if ended:
                        self.video_time.emit("00:00", total_dur)
                        self.log("    播放完成")
                        self._ensure_page_ok(driver)
                        return True
                except Exception:
                    pass

                try:
                    time_els = driver.find_elements(By.CSS_SELECTOR, CSS_VIDEO_TIME)
                    if not time_els:
                        time.sleep(2)
                        ticks += 2
                        continue
                    time_text = time_els[0].get_attribute("innerText").replace("-", "")
                except Exception:
                    time.sleep(2)
                    ticks += 2
                    continue

                if time_text == "00:00":
                    self.video_time.emit("00:00", total_dur)
                    time.sleep(1)
                    self.log("    播放完成")
                    self._ensure_page_ok(driver)
                    return True

                ticks += 1
                self.video_time.emit(time_text, total_dur)

                if time_text == last_time and time_text != "00:00":
                    stuck_count += 1
                    if stuck_count > 12:
                        self.log("    页面卡死，正在刷新...")
                        try:
                            driver.refresh()
                        except Exception:
                            pass
                        time.sleep(3)
                        return False
                else:
                    stuck_count = 0
                    last_time = time_text

                try:
                    play_btn = driver.find_element(By.CSS_SELECTOR, CSS_VIDEO_PLAY_BTN)
                    if play_btn.get_attribute("aria-label") == "Play":
                        try:
                            driver.find_element(By.CSS_SELECTOR, ".public_btn a").click()
                        except Exception:
                            pass
                        stuck_count = 0
                except Exception:
                    pass

                time.sleep(1)
            return False
        finally:
            driver.implicitly_wait(3)

    def _ask_ai(self, question_type: str, question: str, options: list[str]) -> int | list[int]:
        type_hint = {
            "single": "单选题，只返回正确答案序号（如：3）",
            "multi": "多选题，返回所有正确答案序号用逗号分隔（如：1,3,4）",
            "judge": "判断题，只返回正确答案序号（如：1）",
        }
        opts_text = "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(options))
        prompt = f"{question}\n\n选项:\n{opts_text}\n\n要求: {type_hint.get(question_type, '')}"

        for attempt in range(3):
            try:
                resp = requests.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": f"Bearer {self.deepseek_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [
                            {"role": "system", "content": "你是党课考试助手，根据题目选正确答案。只返回序号，不要解释。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0,
                        "max_tokens": 50,
                        "stream": False,
                        "thinking": {"type": "disabled"},
                    },
                    timeout=60,
                )
                data = resp.json()
                if "error" in data:
                    err_code = data["error"].get("code", "")
                    if err_code in ("1302", "1305"):
                        wait = (attempt + 1) * 5
                        time.sleep(wait)
                        continue
                    time.sleep(3)
                    continue

                answer_text = data["choices"][0]["message"]["content"].strip()
                nums = [int(n) for n in re.findall(r"\d+", answer_text)]
                if not nums:
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    return [1] if question_type == "multi" else 1
                if question_type == "multi":
                    return nums
                return nums[0]
            except requests.exceptions.Timeout:
                time.sleep(3)
            except Exception:
                time.sleep(2)
        return [1] if question_type == "multi" else 1

    def _ask_ai_fill_blank(self, question: str) -> str:
        prompt = (
            f"{question}\n\n"
            "要求：这是填空题。只返回最终简短答案，不要解释，不要带序号，不要复述题干。"
        )

        for attempt in range(3):
            try:
                resp = requests.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": f"Bearer {self.deepseek_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是党课考试助手，根据题目填写最可能的简短答案。只返回答案文本，不要解释。",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0,
                        "max_tokens": 80,
                        "stream": False,
                        "thinking": {"type": "disabled"},
                    },
                    timeout=60,
                )
                data = resp.json()
                if "error" in data:
                    err_code = data["error"].get("code", "")
                    if err_code in ("1302", "1305"):
                        time.sleep((attempt + 1) * 5)
                        continue
                    time.sleep(3)
                    continue

                answer_text = data["choices"][0]["message"]["content"].strip()
                answer_text = re.sub(r"^答案[:：]\s*", "", answer_text)
                answer_text = answer_text.strip().strip("“”\"'()（）")
                answer_text = answer_text.splitlines()[0].strip() if answer_text else ""
                if answer_text:
                    return answer_text
                if attempt < 2:
                    time.sleep(1)
                    continue
            except requests.exceptions.Timeout:
                time.sleep(3)
            except Exception:
                time.sleep(2)
        return "无"

    def _get_current_question_html(self, driver) -> str:
        fragments = []
        for selector in (".exam_h2", ".answer_list", ".answer_list_box"):
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if not element.is_displayed():
                        continue
                    fragments.append(
                        driver.execute_script("return arguments[0].outerHTML;", element)
                    )
                except Exception:
                    continue
        return "\n".join(fragments)

    def _click_text_confirm(self, driver) -> bool:
        confirm_xpath = (
            "//*[self::a or self::button]"
            "[not(contains(normalize-space(.), '取消'))]"
            "[contains(normalize-space(.), '确定')"
            " or contains(normalize-space(.), '确认')"
            " or contains(normalize-space(.), '提交')"
            " or contains(normalize-space(.), '交卷')]"
        )
        try:
            for button in driver.find_elements(By.XPATH, confirm_xpath):
                if button.is_displayed():
                    driver.execute_script("arguments[0].click();", button)
                    time.sleep(1)
                    return True
        except Exception:
            pass
        return False

    def _submit_current_exam(self, driver, allow_text_fallback: bool = False):
        self.log("    正在提交...")
        try:
            driver.find_element(By.ID, "submit_exam").click()
            time.sleep(2)
        except Exception:
            pass

        clicked = False
        try:
            confirm = driver.find_element(By.CSS_SELECTOR, CSS_PUBLIC_SUBMIT)
            if confirm.is_displayed():
                confirm.click()
                clicked = True
                time.sleep(1)
        except Exception:
            pass

        if allow_text_fallback and not clicked:
            self._click_text_confirm(driver)

        time.sleep(3)
        self._address_dialogs(driver)

    def _load_task_page_state(self, driver) -> TaskPageState | None:
        if not self._safe_get(driver, TASK_PAGE_URL):
            self.log("  任务页加载失败")
            return None

        time.sleep(2)
        self._address_dialogs(driver)
        return parse_task_page_html(driver.page_source, BASE_URL)

    def _wait_for_comprehensive_exam_ready(self, driver, timeout_secs: int = 15) -> list:
        deadline = time.time() + timeout_secs
        last_count = 0

        while time.time() < deadline:
            self._ensure_page_ok(driver)
            self._address_dialogs(driver)
            q_lis_all = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
            if q_lis_all:
                return q_lis_all

            last_count = len(q_lis_all)
            time.sleep(1)

        if last_count == 0:
            self.log("    综合测试题目列表等待超时")
        return []

    def _wait_for_question_number(
        self,
        driver,
        question_no: int,
        timeout_secs: float = 6,
    ) -> bool:
        deadline = time.time() + timeout_secs
        expected_prefix = f"{question_no}."

        while time.time() < deadline:
            try:
                current_text = driver.find_element(By.CSS_SELECTOR, ".exam_h2").text.strip()
                if current_text.startswith(expected_prefix):
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def _goto_comprehensive_question(self, driver, question_no: int) -> bool:
        if self._wait_for_question_number(driver, question_no, timeout_secs=0.2):
            return True

        moved = False
        try:
            moved = bool(
                driver.execute_script(
                    """
                    if (typeof getQuestion === 'function') {
                        getQuestion(arguments[0]);
                        return true;
                    }
                    return false;
                    """,
                    question_no,
                )
            )
        except Exception:
            moved = False

        if moved and self._wait_for_question_number(driver, question_no):
            return True

        try:
            q_lis_now = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
            target_idx = question_no - 1
            if 0 <= target_idx < len(q_lis_now):
                driver.execute_script("arguments[0].click();", q_lis_now[target_idx])
                return self._wait_for_question_number(driver, question_no)
        except Exception:
            pass
        return False

    def _set_fill_blank_answer(self, driver, target_input, answer_text: str):
        try:
            driver.execute_script(
                """
                arguments[0].focus();
                arguments[0].value = '';
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                """,
                target_input,
                answer_text,
            )
            return
        except Exception:
            pass

        try:
            target_input.clear()
        except Exception:
            pass
        target_input.send_keys(answer_text)

    def _wait_for_question_done(
        self,
        driver,
        question_idx: int,
        question_id: str | None = None,
        timeout_secs: int = 6,
    ) -> bool:
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            try:
                if question_id:
                    q_li = driver.find_element(By.ID, question_id)
                    cls = q_li.get_attribute("class") or ""
                    if "done" in cls:
                        return True
                else:
                    q_lis_now = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
                    if question_idx < len(q_lis_now):
                        cls = q_lis_now[question_idx].get_attribute("class") or ""
                        if "done" in cls:
                            return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def _submit_fill_blank_answer(self, driver, target_input, answer_text: str) -> tuple[bool, str]:
        payload = driver.execute_script(
            """
            const input = arguments[0];
            const rawAnswer = arguments[1] || '';
            const normalized = rawAnswer.replace(/,/g, '，').replace(/\\s*/g, '');
            const qindex = input.getAttribute('qindex') || '';
            const qid = input.getAttribute('qid') || '';

            input.focus();
            input.value = rawAnswer;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));

            let result = false;
            if (typeof answerQuestion === 'function') {
                result = !!answerQuestion(qindex, qid, normalized);
            } else if (window.jQuery) {
                window.jQuery(input).trigger('blur');
            } else {
                input.blur();
            }

            if (window.jQuery) {
                window.jQuery(input).trigger('blur');
            } else {
                input.blur();
            }

            if (result && qid) {
                const item = document.getElementById(qid);
                if (item) {
                    item.classList.add('done');
                }
            }

            return { result, qid, normalized };
            """,
            target_input,
            answer_text,
        )

        return bool(payload.get("result")), str(payload.get("qid") or "")

    def _solve_comprehensive_exam(self, driver, exam_url: str) -> int | None:
        self.question_progress.emit(0, 0)
        if not self._safe_get(driver, exam_url):
            self.log("    综合测试页面加载失败")
            return None

        time.sleep(4)
        q_lis_all = self._wait_for_comprehensive_exam_ready(driver)
        q_lis = [li for li in q_lis_all if li.is_displayed()]
        if not q_lis:
            q_lis = q_lis_all
        if not q_lis:
            page_text = driver.page_source
            if is_unauthorized_status(page_text):
                self.log("    未被授权")
            else:
                self.log("    未找到综合测试题目列表")
            return None

        total_q = len(q_lis)
        done_set = set()
        for i, q in enumerate(q_lis):
            cls = q.get_attribute("class") or ""
            if "done" in cls:
                done_set.add(i)

        first_undone = total_q
        for i in range(total_q):
            if i not in done_set:
                first_undone = i
                break

        self.log(f"    共 {total_q} 道题, 已作答 {len(done_set)} 题")
        if done_set:
            self.log("    恢复未完成考试")

        if first_undone < total_q:
            self._goto_comprehensive_question(driver, first_undone + 1)
            self._address_dialogs(driver)
        else:
            self.log("    全部题目已作答，直接提交")
            self.question_progress.emit(total_q, total_q)

        for idx in range(first_undone, total_q):
            if self._stop:
                return None

            if idx > first_undone:
                if not self._goto_comprehensive_question(driver, idx + 1):
                    self.log(f"    [{idx+1}/{total_q}] 切换题目失败，跳过")
                    continue

            self._address_dialogs(driver)

            try:
                h2 = driver.find_element(By.CSS_SELECTOR, ".exam_h2")
                question_text = h2.text.strip()
            except Exception:
                self.log(f"    [{idx+1}/{total_q}] 题目加载失败，跳过")
                continue

            question_type = detect_comprehensive_question_type(
                self._get_current_question_html(driver)
            )
            if not question_type:
                self.log(f"    [{idx+1}/{total_q}] 未识别题型，跳过")
                continue

            self.question_progress.emit(idx + 1, total_q)

            if question_type == "fill_blank":
                self.log(f"    [{idx+1}/{total_q}] [填空] {question_text[:50]}...")
                answer_text = self._ask_ai_fill_blank(question_text)
                self.log(f"      答案: {answer_text}")

                fill_inputs = driver.find_elements(
                    By.CSS_SELECTOR,
                    ".answer_list_box input.summary_question",
                )
                target_input = None
                for fill_input in fill_inputs:
                    if fill_input.is_displayed():
                        target_input = fill_input
                        break
                if target_input is None and fill_inputs:
                    target_input = fill_inputs[0]
                if target_input is None:
                    self.log("      未找到填空输入框")
                    continue

                self._set_fill_blank_answer(driver, target_input, answer_text)
                time.sleep(0.2)
                question_id = target_input.get_attribute("qid") or ""

                submitted, submitted_qid = self._submit_fill_blank_answer(
                    driver,
                    target_input,
                    answer_text,
                )
                wait_qid = submitted_qid or question_id

                saved = self._wait_for_question_done(driver, idx, wait_qid)
                if submitted or saved:
                    self.log("      已保存")
                else:
                    self.log("      保存失败，继续尝试下一题")
                self._address_dialogs(driver)
                continue

            inputs = driver.find_elements(
                By.CSS_SELECTOR,
                ".answer_list input[type='radio'], .answer_list input[type='checkbox'], "
                ".answer_list_box input[type='radio'], .answer_list_box input[type='checkbox']",
            )
            if not inputs:
                self.log(f"    [{idx+1}/{total_q}] 无选项，跳过")
                continue

            options = []
            for inp in inputs:
                try:
                    option_text = inp.find_element(By.XPATH, "..").text.strip()
                except Exception:
                    option_text = ""
                options.append(option_text)

            prefix = {"single": "[单选]", "multi": "[多选]", "judge": "[判断]"}
            self.log(f"    [{idx+1}/{total_q}] {prefix[question_type]} {question_text[:50]}...")
            answer = self._ask_ai(question_type, question_text, options)

            inputs2 = driver.find_elements(
                By.CSS_SELECTOR,
                ".answer_list input[type='radio'], .answer_list input[type='checkbox'], "
                ".answer_list_box input[type='radio'], .answer_list_box input[type='checkbox']",
            )

            if isinstance(answer, list):
                self.log(f"      答案: {answer}")
                for a in answer:
                    if 1 <= a <= len(inputs2):
                        driver.execute_script("arguments[0].click();", inputs2[a - 1])
                        time.sleep(0.1)
            else:
                self.log(f"      答案: {answer}")
                sel = max(1, min(answer, len(inputs2)))
                driver.execute_script("arguments[0].click();", inputs2[sel - 1])
                time.sleep(0.1)

            time.sleep(0.3)

        self._submit_current_exam(driver, allow_text_fallback=True)

        score = self._get_score(driver)
        if score is not None:
            self.log(f"    得分: {score} 分")
            self.exam_score.emit(score)

        self.log("    综合测试交卷完成")
        return score

    def _solve_exam(self, driver, lesson_id: int) -> bool:
        self.question_progress.emit(0, 0)
        exam_url = f"{BASE_URL}/jjfz/lesson/exam?lesson_id={lesson_id}"
        self._safe_get(driver, exam_url)
        time.sleep(3)
        self._address_dialogs(driver)

        q_lis = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
        if not q_lis:
            self.log("    未找到题目列表")
            return False

        total_q = len(q_lis)
        done_set = set()
        for i, q in enumerate(q_lis):
            cls = q.get_attribute("class") or ""
            if "done" in cls:
                done_set.add(i)

        first_undone = total_q
        for i in range(total_q):
            if i not in done_set:
                first_undone = i
                break

        self.log(f"    共 {total_q} 道题, 已作答 {len(done_set)} 题")

        if first_undone >= total_q:
            self.log("    全部已作答，跳过")
            return False

        if first_undone > 0:
            q_lis[first_undone].click()
            time.sleep(0.8)
            self._address_dialogs(driver)

        for idx in range(first_undone, total_q):
            if self._stop:
                return False

            if idx > first_undone:
                try:
                    nxt = driver.find_element(By.ID, "next_question")
                    nxt.click()
                except Exception:
                    try:
                        q_lis_now = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
                        if idx < len(q_lis_now):
                            driver.execute_script("arguments[0].click();", q_lis_now[idx])
                    except Exception:
                        pass
                time.sleep(0.8)

            self._address_dialogs(driver)

            try:
                h2 = driver.find_element(By.CSS_SELECTOR, ".exam_h2")
                question_text = h2.text.strip()
            except Exception:
                self.log(f"    [{idx+1}/{total_q}] 题目加载失败，跳过")
                continue

            inputs = driver.find_elements(
                By.CSS_SELECTOR,
                ".answer_list input[type='radio'], .answer_list input[type='checkbox'], "
                ".answer_list_box input[type='radio'], .answer_list_box input[type='checkbox']")
            if not inputs:
                self.log(f"    [{idx+1}/{total_q}] 无选项，跳过")
                continue

            itype = "single"
            if inputs[0].get_attribute("type") == "checkbox":
                itype = "multi"
            elif len(inputs) == 2:
                itype = "judge"

            options = []
            for inp in inputs:
                try:
                    txt = inp.find_element(By.XPATH, "..").text.strip()
                except Exception:
                    txt = ""
                options.append(txt)

            prefix = {"single": "[单选]", "multi": "[多选]", "judge": "[判断]"}
            self.question_progress.emit(idx + 1, total_q)
            self.log(f"    [{idx+1}/{total_q}] {prefix[itype]} {question_text[:50]}...")
            answer = self._ask_ai(itype, question_text, options)

            inputs2 = driver.find_elements(
                By.CSS_SELECTOR,
                ".answer_list input[type='radio'], .answer_list input[type='checkbox'], "
                ".answer_list_box input[type='radio'], .answer_list_box input[type='checkbox']")

            if isinstance(answer, list):
                self.log(f"      答案: {answer}")
                for a in answer:
                    if 1 <= a <= len(inputs2):
                        driver.execute_script("arguments[0].click();", inputs2[a - 1])
                        time.sleep(0.1)
            else:
                self.log(f"      答案: {answer}")
                sel = max(1, min(answer, len(inputs2)))
                driver.execute_script("arguments[0].click();", inputs2[sel - 1])
                time.sleep(0.1)

            time.sleep(0.3)

        self.log("    正在提交...")
        try:
            driver.find_element(By.ID, "submit_exam").click()
            time.sleep(2)
        except Exception:
            pass

        try:
            confirm = driver.find_element(By.CSS_SELECTOR, CSS_PUBLIC_SUBMIT)
            if confirm.is_displayed():
                confirm.click()
                time.sleep(1)
        except Exception:
            pass

        time.sleep(3)
        self._address_dialogs(driver)

        score = self._get_score(driver)
        if score is not None:
            self.log(f"    得分: {score} 分")
            self.exam_score.emit(score)

        self.log("    交卷完成")
        return True

    def _get_score(self, driver):
        try:
            body = driver.page_source
            m = re.search(r'score_rate\d?["\']?\s*[>]\s*(\d+)\s*<', body)
            if m:
                return int(m.group(1))
            m = re.search(r'(\d+)\s*分', body)
            if m:
                return int(m.group(1))
            return None
        except Exception:
            return None

    def _run_automation(self):
        self.log("正在启动浏览器...")
        options = Options()
        options.add_argument("--window-size=1280,720")
        options.add_argument("--autoplay-policy=no-user-gesture-required")
        options.add_argument("--mute-audio")
        options.add_argument("--disable-infobars")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        driver = None
        driver = webdriver.Chrome(options=options)
        driver.implicitly_wait(3)
        self.log("浏览器已启动")

        try:
            self.log("正在登录...")
            self._safe_get(driver, f"{BASE_URL}/login/")
            time.sleep(2)

            inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='text']")
            if not inputs:
                self.login_failed.emit("无法找到登录表单")
                return
            inputs[0].send_keys(self.username)

            pwd_els = driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
            if pwd_els:
                pwd_els[0].send_keys(self.password)

            captcha_input = driver.find_elements(By.CSS_SELECTOR, "input[placeholder='验证码']")
            captcha_img = driver.find_elements(By.CSS_SELECTOR, ".login_piccheck_img")
            if not captcha_input or not captcha_img:
                self.login_failed.emit("无法找到验证码输入框")
                return

            ocr = ddddocr.DdddOcr(show_ad=False)
            for a in range(1, 11):
                if self._stop:
                    return
                src = captcha_img[0].get_attribute("src")
                _, b64 = src.split(",", 1)
                img = base64.b64decode(b64)
                c = re.sub(r"[^a-zA-Z0-9]", "", ocr.classification(img))
                self.log(f"  验证码: {c}")
                captcha_input[0].clear()
                captcha_input[0].send_keys(c)
                driver.find_element(By.CSS_SELECTOR, ".login_btn").click()
                time.sleep(3)
                cur = driver.current_url
                if "/user/" in cur or "/jjfz/" in cur or "/guide" in cur:
                    self.log("  登录成功!")
                    break
                captcha_img[0].click()
                time.sleep(1)
            else:
                self.login_failed.emit("登录失败, 请检查账号密码")
                return

            self._video_phase(driver)
            if self._stop:
                return

            exam_ok = self._exam_phase(driver)
            if self._stop or not exam_ok:
                return

            self._comprehensive_phase(driver)

        finally:
            if driver:
                driver.quit()
            self.log("浏览器已关闭")

    def _check_course_status(self, driver):
        courses = []
        for li in driver.find_elements(By.CSS_SELECTOR, ".lesson_c_ul li"):
            try:
                a = li.find_element(By.CSS_SELECTOR, "a[href*='lecture?lesson_id=']")
                lid = int(re.search(r"lesson_id=(\d+)", a.get_attribute("href")).group(1))
                h2 = li.find_element(By.CSS_SELECTOR, "h2").text.strip()
            except Exception:
                continue

            total = completed = 0
            for dd in li.find_elements(By.CSS_SELECTOR, ".lesson_center_dl dd"):
                t = dd.text.strip()
                m = re.search(r"(\d+)", t)
                if not m:
                    continue
                v = int(m.group(1))
                if "\u5b8c\u6210" in t:
                    completed = v
                else:
                    total = v

            has_label = False
            try:
                label = li.find_element(By.CSS_SELECTOR, ".lesson_label")
                if label.is_displayed() and "\u901a\u8fc7" in label.text:
                    has_label = True
            except Exception:
                pass

            exam_clickable = False
            try:
                exam = li.find_element(By.CSS_SELECTOR, "a.self_text")
                if exam.is_displayed():
                    exam_clickable = True
            except Exception:
                pass

            videos_done = (total > 0 and total == completed and exam_clickable)

            courses.append({
                "lesson_id": lid,
                "name": h2,
                "has_label": has_label,
                "exam_clickable": exam_clickable,
                "total_required": total,
                "completed_required": completed,
                "videos_done": videos_done,
            })
        return courses

    def _learn_videos_for_course(self, driver, lid):
        v_url = f"{BASE_URL}/jjfz/lesson/video?lesson_id={lid}&required=1"
        self._safe_get(driver, v_url)
        self._address_dialogs(driver)

        required_list = driver.find_elements(By.CSS_SELECTOR, CSS_REQUIRED_LIST)
        if not required_list:
            required_list = driver.find_elements(By.CSS_SELECTOR, "a[href*='play?v_id=']")
            required_list = [a for a in required_list
                             if a.find_elements(By.CSS_SELECTOR, ".r_read")
                             or "\u5fc5\u4fee" in (a.get_attribute("href") or "")]
            if not required_list:
                required_list = driver.find_elements(By.CSS_SELECTOR, ".l_list_right h2 a[href*='play']")
        if not required_list:
            self.log("  无必修课")
            return

        required_page = driver.current_url

        for req_i in range(len(required_list)):
            if self._stop:
                return
            rlist = driver.find_elements(By.CSS_SELECTOR, CSS_REQUIRED_LIST)
            if req_i >= len(rlist):
                break

            self.log(f"  必修课 {req_i+1}/{len(required_list)}")
            self._remove_blank(driver)
            mh2 = driver.current_window_handle
            rlist[req_i].send_keys(Keys.ENTER)
            time.sleep(3)
            self._close_extra_windows(driver, mh2)
            self._address_dialogs(driver)
            self._ensure_page_ok(driver)
            self._mute_video(driver)

            sidebars = driver.find_elements(By.CSS_SELECTOR, CSS_SIDEBAR_LINKS)
            if not sidebars:
                self.log("    无视频")
            else:
                self.video_info.emit(0, len(sidebars))
                for seg_i in range(len(sidebars)):
                    if self._stop:
                        return
                    self._remove_blank(driver)
                    mh3 = driver.current_window_handle
                    sb = driver.find_elements(By.CSS_SELECTOR, CSS_SIDEBAR_LINKS)
                    if seg_i >= len(sb):
                        break
                    style = sb[seg_i].get_attribute("style") or ""
                    if "red" in style:
                        self.log(f"    视频{seg_i+1} 已完成，跳过")
                        self.video_info.emit(seg_i + 1, len(sidebars))
                        continue
                    self.video_info.emit(seg_i + 1, len(sidebars))
                    self.log(f"    视频{seg_i+1}/{len(sidebars)} 播放中...")
                    sb[seg_i].send_keys(Keys.ENTER)
                    time.sleep(1)
                    self._close_extra_windows(driver, mh3)
                    self._clear_overlays(driver)
                    self._address_dialogs(driver)
                    self._mute_video(driver)
                    for retry in range(3):
                        ok = self._watch_video(driver)
                        if ok or self._stop:
                            break
                        self.log(f"    重试 ({retry+1}/3)...")
                        time.sleep(2)

            self._safe_get(driver, required_page)

    def _video_phase(self, driver):
        self.phase_update.emit("视频学习阶段")
        self.log("\n" + "=" * 60)
        self.log("第一阶段：必修视频学习")
        self.log("=" * 60)

        while not self._stop:
            self._safe_get(driver, f"{BASE_URL}/jjfz/lesson")
            time.sleep(3)

            courses = self._check_course_status(driver)
            total = len(courses)
            done_label = sum(1 for c in courses if c["has_label"])
            done_videos = sum(1 for c in courses if c["videos_done"])
            self.log(f"  状态: {done_label}已通过 {done_videos}视频已完成 / {total}门课程")

            pending = [c for c in courses if not c["has_label"] and not c["videos_done"]]
            if not pending:
                self.log("  全部课程必修视频已完成")
                break

            c = pending[0]
            idx = next(i for i, x in enumerate(courses) if x["lesson_id"] == c["lesson_id"])
            self.course_start.emit(idx + 1, total, c["name"])
            self.log(f"\n[视频 {idx+1}/{total}] {c['name']} (必读{c['total_required']}, 已完成{c['completed_required']})")
            self._learn_videos_for_course(driver, c["lesson_id"])

        self.status_update.emit("视频学习完成")

    def _exam_phase(self, driver) -> bool:
        self.phase_update.emit("考试阶段")
        self.log("\n" + "=" * 60)
        self.log("第二阶段：自动答题")
        self.log("=" * 60)

        back_to_video = 0

        while not self._stop:
            self._safe_get(driver, f"{BASE_URL}/jjfz/lesson")
            time.sleep(3)

            courses = self._check_course_status(driver)
            done_label = sum(1 for c in courses if c["has_label"])
            total = len(courses)
            self.log(f"  状态: {done_label}已通过 / {total}门课程")

            pending = [c for c in courses if not c["has_label"]]
            if not pending:
                self.log("  全部课程自测已通过")
                break

            c = pending[0]
            if not c["exam_clickable"]:
                back_to_video += 1
                if back_to_video > 3:
                    self.log(f"  回退已达{back_to_video}次，{c['name']}必修视频仍未完成，退出")
                    return False
                self.log(f"  {c['name']} 自测按钮不可用，第{back_to_video}次回退至视频阶段")
                self._video_phase(driver)
                continue

            back_to_video = 0
            idx = next(i for i, x in enumerate(courses) if x["lesson_id"] == c["lesson_id"])
            todo = len(pending)
            self.course_start.emit(idx + 1, total, c["name"])
            self.exam_progress_info.emit(done_label, total)
            self.log(f"\n[考试 {idx+1}/{total}] lesson_id={c['lesson_id']} {c['name']}")
            self._solve_exam(driver, c["lesson_id"])

        self.status_update.emit("全部课程学习完成!")
        self.log("\n全部课程学习完成!")
        return True

    def _comprehensive_phase(self, driver) -> bool:
        self.phase_update.emit("综合测试阶段")
        self.log("\n" + "=" * 60)
        self.log("第三阶段：综合测试")
        self.log("=" * 60)

        state = self._load_task_page_state(driver)
        if state is None:
            self.log("  任务页状态解析失败，退出综合测试阶段")
            return False

        self.log(f"  理论学习状态: {state.theory_status_text or '未识别'}")
        self.log(f"  综合测试状态: {state.comprehensive_status_text or '未识别'}")

        if not state.theory_completed:
            self.log("  理论学习未完成")
            self.status_update.emit("理论学习未完成")
            return False

        if state.comprehensive_completed:
            self.log("  综合测试已完成")
            self.status_update.emit("综合测试已完成")
            return True

        if is_unauthorized_status(
            state.comprehensive_status_text or state.comprehensive_cta_text
        ):
            self.log("  未被授权")
            self.status_update.emit("未被授权")
            return False

        if not state.comprehensive_cta_href:
            self.log("  综合测试入口不可用，退出")
            return False

        if COMPREHENSIVE_EXAM_PATH not in state.comprehensive_cta_href:
            self.log(f"  综合测试入口异常: {state.comprehensive_cta_href}")
            return False

        exam_url = state.comprehensive_cta_href
        attempt = 1

        while not self._stop:
            required_score = get_comprehensive_required_score(attempt)
            self.log(
                f"  第{attempt}次综合测试，当前目标分数: {required_score} 分"
            )
            self.log(
                f"  进入综合测试: {state.comprehensive_cta_text or '去测试'} -> "
                f"{exam_url}"
            )

            score = self._solve_comprehensive_exam(driver, exam_url)
            if score is None:
                self.log("  未能识别综合测试分数，停止重试")
                self.status_update.emit("综合测试未能获取分数")
                return False

            if is_comprehensive_score_accepted(score, attempt):
                if score < 80:
                    self.log("  连续3次未达到80分，最低标准调整为60分，本次按60分标准通过")
                self.status_update.emit("全部课程与综合测试完成!")
                self.log("\n全部课程与综合测试完成!")
                return True

            if attempt == 3:
                self.log("  连续3次未达到80分，最低标准调整为60分")
            else:
                self.log(
                    f"  第{attempt}次得分 {score} 分，未达到 {required_score} 分，准备重试"
                )

            if not self._safe_get(driver, TASK_PAGE_URL):
                self.log("  返回任务页失败，无法继续综合测试")
                return False

            time.sleep(2)
            self._address_dialogs(driver)
            state = parse_task_page_html(driver.page_source, BASE_URL)
            self.log(f"  综合测试状态: {state.comprehensive_status_text or '未识别'}")

            if is_unauthorized_status(
                state.comprehensive_status_text or state.comprehensive_cta_text
            ):
                self.log("  未被授权")
                return False

            if state.comprehensive_completed and score < 60:
                self.log("  任务页显示综合测试已完成，但当前分数低于60分，停止重试")
                return False

            if not state.comprehensive_cta_href:
                self.log("  综合测试入口不可用，无法继续重试")
                return False

            if COMPREHENSIVE_EXAM_PATH not in state.comprehensive_cta_href:
                self.log(f"  综合测试入口异常: {state.comprehensive_cta_href}")
                return False

            exam_url = state.comprehensive_cta_href
            attempt += 1

        return False


class GradientProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self._gradient_start = QColor(COLOURS["lt1"])
        self._gradient_end = QColor(COLOURS["accent3"])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        radius = 4

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(40, 20, 16))
        painter.drawRoundedRect(rect, radius, radius)

        if self.maximum() > 0 and self.value() > 0:
            w = int(rect.width() * self.value() / self.maximum())
            if w > 0:
                gradient = QLinearGradient(rect.topLeft(), rect.topRight())
                gradient.setColorAt(0, self._gradient_start)
                gradient.setColorAt(1, self._gradient_end)
                painter.setBrush(gradient)
                painter.drawRoundedRect(rect.adjusted(0, 0, w - rect.width(), 0), radius, radius)

        painter.end()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self._fade_anim = None
        self._init_ui()
        self._init_acrylic()
        self._load_settings()

    def _init_acrylic(self):
        try:
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_SYSTEMBACKDROP_TYPE = 38
            DWMSBT_MAINWINDOW = 2
            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                ctypes.byref(ctypes.c_int(DWMSBT_MAINWINDOW)),
                ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

    def _init_ui(self):
        self.setWindowTitle("RPA-党课自动学习助手")
        self.setMinimumSize(900, 680)
        self.resize(960, 700)

        self.setStyleSheet(self._style())

        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("RPA · 党课自动学习助手")
        title.setObjectName("mainTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("ddddocr 验证码识别 · Selenium 视频自动播放 · 进度追踪")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("用户名"))
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("学号/工号")
        self.username_input.setMinimumWidth(160)
        row1.addWidget(self.username_input)

        row1.addSpacing(16)
        row1.addWidget(QLabel("密  码"))
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("密码")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumWidth(160)
        row1.addWidget(self.password_input)

        card_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("API Key"))
        self.deepseek_key_input = QLineEdit()
        self.deepseek_key_input.setPlaceholderText("请输入 DeepSeek API Key")
        self.deepseek_key_input.setEchoMode(QLineEdit.Password)
        self.deepseek_key_input.setMinimumWidth(420)
        row2.addWidget(self.deepseek_key_input)

        self.remember_check = QCheckBox("保存本地配置")
        row2.addWidget(self.remember_check)
        row2.addStretch()
        card_layout.addLayout(row2)

        layout.addWidget(card)

        progress_card = QFrame()
        progress_card.setObjectName("card")
        pc_layout = QVBoxLayout(progress_card)
        pc_layout.setContentsMargins(16, 12, 16, 12)
        pc_layout.setSpacing(8)

        self.course_label = QLabel("就绪 · 等待开始")
        self.course_label.setObjectName("progressLabel")
        pc_layout.addWidget(self.course_label)

        course_row = QHBoxLayout()
        course_row.addWidget(QLabel("课程进度"))
        self.course_progress = GradientProgressBar()
        self.course_progress.setMaximum(0)
        self.course_progress.setValue(0)
        course_row.addWidget(self.course_progress)
        self.course_count_label = QLabel("—")
        self.course_count_label.setObjectName("countLabel")
        course_row.addWidget(self.course_count_label)
        pc_layout.addLayout(course_row)

        video_row = QHBoxLayout()
        video_row.addWidget(QLabel("视频列表"))
        self.video_progress = GradientProgressBar()
        self.video_progress.setMaximum(0)
        self.video_progress.setValue(0)
        video_row.addWidget(self.video_progress)
        self.video_count_label = QLabel("—")
        self.video_count_label.setObjectName("countLabel")
        video_row.addWidget(self.video_count_label)
        pc_layout.addLayout(video_row)

        single_row = QHBoxLayout()
        single_row.addWidget(QLabel("播放进度"))
        self.single_progress = GradientProgressBar()
        self.single_progress.setMaximum(100)
        self.single_progress.setValue(0)
        self.single_progress.setFormat("")
        self.single_progress.setTextVisible(False)
        single_row.addWidget(self.single_progress)
        self.single_time_label = QLabel("")
        self.single_time_label.setObjectName("countLabel")
        self.single_time_label.setMinimumWidth(100)
        single_row.addWidget(self.single_time_label)
        pc_layout.addLayout(single_row)

        exam_row = QHBoxLayout()
        exam_row.addWidget(QLabel("解题进度"))
        self.exam_progress = GradientProgressBar()
        self.exam_progress.setMaximum(0)
        self.exam_progress.setValue(0)
        exam_row.addWidget(self.exam_progress)
        self.exam_count_label = QLabel("—")
        self.exam_count_label.setObjectName("countLabel")
        exam_row.addWidget(self.exam_count_label)
        pc_layout.addLayout(exam_row)

        layout.addWidget(progress_card)

        log_card = QFrame()
        log_card.setObjectName("card")
        lc_layout = QVBoxLayout(log_card)
        lc_layout.setContentsMargins(12, 8, 12, 8)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setObjectName("logOutput")
        lc_layout.addWidget(self.log_output)
        layout.addWidget(log_card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.start_btn = QPushButton("▶  开始学习")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setMinimumWidth(160)
        self.start_btn.setMinimumHeight(42)
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■  停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setMinimumWidth(120)
        self.stop_btn.setMinimumHeight(42)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.start_btn.setGraphicsEffect(shadow)

    def _style(self) -> str:
        bg = COLOURS["bg"]
        surf = COLOURS["surface"]
        txt = COLOURS["text"]
        txt2 = COLOURS["text2"]
        a1 = COLOURS["accent1"]
        a2 = COLOURS["accent2"]
        a3 = COLOURS["accent3"]

        return f"""
            QMainWindow {{
                background-color: {bg};
            }}
            #centralWidget {{
                background-color: {bg};
            }}
            #mainTitle {{
                font-size: 20px;
                font-weight: bold;
                color: {txt2};
                letter-spacing: 2px;
            }}
            #subtitle {{
                font-size: 11px;
                color: {txt};
                opacity: 0.7;
            }}
            #card {{
                background-color: {surf};
                border: 1px solid #3A1A14;
                border-radius: 10px;
            }}
            #progressLabel {{
                font-size: 12px;
                font-weight: bold;
                color: {txt2};
            }}
            #countLabel {{
                font-size: 11px;
                color: {txt2};
                min-width: 44px;
            }}
            #logOutput {{
                background-color: {bg};
                border: none;
                color: {txt};
                font-family: Consolas, Microsoft YaHei;
                font-size: 11px;
                border-radius: 6px;
                padding: 6px;
            }}
            #startBtn {{
                background-color: {a2};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }}
            #startBtn:hover {{
                background-color: {a3};
            }}
            #startBtn:pressed {{
                background-color: {COLOURS['accent4']};
            }}
            #startBtn:disabled {{
                background-color: #3A1A14;
                color: #886A62;
            }}
            #stopBtn {{
                background-color: #3A1A14;
                color: {txt};
                border: 1px solid #553028;
                border-radius: 6px;
                font-size: 14px;
            }}
            #stopBtn:hover {{
                background-color: #4A2018;
            }}
            #stopBtn:disabled {{
                background-color: #2A100E;
                color: #553028;
            }}
            QLabel {{
                color: {txt};
                font-size: 12px;
            }}
            QLineEdit {{
                background-color: {bg};
                border: 1px solid #553028;
                border-radius: 5px;
                color: {txt2};
                padding: 4px 8px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {a1};
            }}
            QCheckBox {{
                color: {txt};
                font-size: 12px;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 1px solid #553028;
                background-color: {bg};
            }}
            QCheckBox::indicator:checked {{
                background-color: {a1};
                border-color: {a1};
            }}
            QProgressBar {{
                background-color: {bg};
                border: none;
                border-radius: 4px;
                height: 16px;
            }}
            QProgressBar::chunk {{
                background-color: {a1};
                border-radius: 4px;
            }}
            QGroupBox {{
                color: {txt2};
                font-weight: bold;
                border: 1px solid #3A1A14;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 16px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 2px 8px;
            }}
            QScrollBar:vertical {{
                background: {bg};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: #553028;
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """

    def _load_settings(self):
        s = load_settings()
        if s.get("username"):
            self.username_input.setText(s["username"])
        if s.get("password"):
            self.password_input.setText(s["password"])
        if s.get("deepseek_key"):
            self.deepseek_key_input.setText(s["deepseek_key"])
        self.remember_check.setChecked(bool(s.get("remember")))

    def _append_log(self, msg: str):
        self.log_output.moveCursor(QTextCursor.End)
        self.log_output.insertPlainText(msg + "\n")
        sb = self.log_output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_start(self):
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()
        deepseek_key = self.deepseek_key_input.text().strip()
        if not username or not password or not deepseek_key:
            QMessageBox.warning(self, "提示", "请输入用户名、密码和 DeepSeek API Key")
            return

        save_settings(username, password, deepseek_key, self.remember_check.isChecked())

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.course_progress.setValue(0)
        self.course_progress.setMaximum(0)
        self.video_progress.setValue(0)
        self.video_progress.setMaximum(0)
        self.single_progress.setValue(0)
        self.course_count_label.setText("—")
        self.video_count_label.setText("—")
        self.single_time_label.setText("")
        self.exam_progress.setValue(0)
        self.exam_progress.setMaximum(0)
        self.exam_count_label.setText("—")
        self.log_output.clear()

        self.worker = WatcherWorker(username, password, deepseek_key)
        self.worker.log_msg.connect(self._append_log)
        self.worker.course_start.connect(self._on_course_start)
        self.worker.course_done.connect(self._on_course_done)
        self.worker.video_info.connect(self._on_video_info)
        self.worker.video_time.connect(self._on_video_time)
        self.worker.status_update.connect(self._on_status)
        self.worker.finished.connect(self._on_finished)
        self.worker.login_failed.connect(self._on_login_failed)
        self.worker.phase_update.connect(self._on_phase_update)
        self.worker.exam_score.connect(self._on_exam_score)
        self.worker.exam_progress_info.connect(self._on_exam_progress_info)
        self.worker.question_progress.connect(self._on_question_progress)
        self.worker.start()

        self._animate_button(self.start_btn)

    def _on_stop(self):
        if self.worker:
            self.worker.stop()
            self._append_log("\n[已停止]")
        self.stop_btn.setEnabled(False)

    def _animate_button(self, btn):
        anim = QPropertyAnimation(btn, b"geometry")
        geo = btn.geometry()
        anim.setDuration(200)
        anim.setStartValue(geo.adjusted(-2, -1, 2, 1))
        anim.setEndValue(geo)
        anim.setEasingCurve(QEasingCurve.OutBack)
        anim.start()

    def _on_course_start(self, current: int, total: int, name: str):
        self.course_progress.setMaximum(total)
        self.course_progress.setValue(current)
        self.course_count_label.setText(f"{current}/{total}")
        self.course_label.setText(f"正在学习: {name}")

    def _on_course_done(self, current: int):
        self.course_progress.setValue(current)
        self.course_count_label.setText(f"{current}/{self.course_progress.maximum()}")
        self.video_progress.setValue(0)
        self.video_progress.setMaximum(0)
        self.video_count_label.setText("—")

    def _on_video_info(self, current: int, total: int):
        if total > 0:
            self.video_progress.setMaximum(total)
        self.video_progress.setValue(current)
        self.video_count_label.setText(f"{current}/{total}" if total > 0 else "—")

    def _on_video_time(self, remaining: str, total_secs: int):
        parts = remaining.split(":")
        if len(parts) == 2:
            try:
                rmins = int(parts[0])
                rsecs = int(parts[1])
                remaining_secs = rmins * 60 + rsecs
                elapsed_secs = max(0, total_secs - remaining_secs) if total_secs > 0 else 0

                if total_secs > 0:
                    pct = elapsed_secs * 100 // total_secs
                    self.single_progress.setValue(min(100, max(0, pct)))
                    tmin = total_secs // 60
                    tsec = total_secs % 60
                    total_str = f"{tmin:02d}:{tsec:02d}"
                else:
                    total_str = "??:??"

                emin = elapsed_secs // 60
                esec = elapsed_secs % 60
                elapsed_str = f"{emin:02d}:{esec:02d}"
                self.single_time_label.setText(f"{elapsed_str} / {total_str}")
            except ValueError:
                pass
        else:
            self.single_time_label.setText(remaining)

    def _on_phase_update(self, msg: str):
        self.course_label.setText(msg)

    def _on_exam_score(self, score: int):
        pass

    def _on_exam_progress_info(self, current: int, total: int):
        pass

    def _on_question_progress(self, current: int, total: int):
        self.exam_progress.setMaximum(total)
        self.exam_progress.setValue(current)
        self.exam_count_label.setText(f"{current}/{total}")

    def _on_status(self, msg: str):
        self.course_label.setText(msg)

    def _on_login_failed(self, msg: str):
        QMessageBox.critical(self, "登录失败", msg)
        self._on_finished()

    def _on_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if self.worker:
            self.worker.quit()
            self.worker.wait(5000)
            self.worker = None

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.quit()
            self.worker.wait(5000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
