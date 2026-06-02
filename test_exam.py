import base64, hashlib, re, sys, time

sys.stdout.reconfigure(encoding="utf-8")

import ddddocr, requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE_URL = "https://dxpx.uestc.edu.cn"
USERNAME = "2024090906010"
PASSWORD = "kD-7VXkCAGt7AEE"
DEEPSEEK_KEY = "sk-88da5667472d4611a521faab31efb1ea"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"

CSS_VIDEO_TIME = 'div[aria-label="Current time"]'
CSS_PUBLIC_SUBMIT = "a.public_submit"
CSS_PUBLIC_CANCEL = "a.public_cancel"
CSS_VIDEO_PLAY_BTN = "#wrapper > div > div.plyr__controls > button:nth-child(1)"


class ExamTester:
    def start_browser(self):
        print("[*] 启动浏览器", flush=True)
        options = Options()
        options.add_argument("--window-size=1280,720")
        options.add_argument("--autoplay-policy=no-user-gesture-required")
        options.add_argument("--mute-audio")
        driver = webdriver.Chrome(options=options)
        driver.implicitly_wait(3)
        return driver

    def login(self, driver):
        print("[*] 登录中...", flush=True)
        driver.get(f"{BASE_URL}/login/")
        time.sleep(2)
        driver.find_element(By.CSS_SELECTOR, "input[type='text']").send_keys(USERNAME)
        p = driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
        if p:
            p[0].send_keys(PASSWORD)
        ci = driver.find_element(By.CSS_SELECTOR, "input[placeholder='验证码']")
        cimg = driver.find_element(By.CSS_SELECTOR, ".login_piccheck_img")
        ocr = ddddocr.DdddOcr(show_ad=False)
        for _ in range(10):
            src = cimg.get_attribute("src")
            _, b64 = src.split(",", 1)
            c = re.sub(r"[^a-zA-Z0-9]", "", ocr.classification(base64.b64decode(b64)))
            print(f"  验证码: {c}", flush=True)
            ci.clear()
            ci.send_keys(c)
            driver.find_element(By.CSS_SELECTOR, ".login_btn").click()
            time.sleep(3)
            if "/user/" in driver.current_url or "/jjfz/" in driver.current_url:
                print("  登录成功!", flush=True)
                return
            cimg.click()
            time.sleep(1)
        raise RuntimeError("登录失败")

    def address_dialogs(self, driver):
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

    def ask_ai(self, question_type, question, options, retries=3):
        type_hint = {
            "radio": "单选题，只返回正确答案序号（如：3）",
            "checkbox": "多选题，返回所有正确答案序号用逗号分隔（如：1,3,4）",
            "judge": "判断题，只返回正确答案序号（如：1）",
        }
        opts = "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(options))
        prompt = f"{question}\n\n选项:\n{opts}\n\n{type_hint.get(question_type, '')}"

        for attempt in range(retries):
            try:
                resp = requests.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_KEY}",
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
                    time.sleep((attempt + 1) * 3)
                    continue
                answer_text = data["choices"][0]["message"]["content"].strip()
                nums = [int(n) for n in re.findall(r"\d+", answer_text)]
                if not nums:
                    if attempt < retries - 1:
                        time.sleep(1)
                        continue
                    return [1] if question_type == "checkbox" else 1
                return nums if question_type == "checkbox" else nums[0]
            except requests.exceptions.Timeout:
                time.sleep(3)
            except Exception:
                time.sleep(2)
        return [1] if question_type == "checkbox" else 1

    def run_exam(self, driver, lesson_id, course_name=""):
        print(f"\n{'='*60}", flush=True)
        print(f"  开始考试: lesson_id={lesson_id} {course_name}", flush=True)
        print(f"{'='*60}", flush=True)

        driver.get(f"{BASE_URL}/jjfz/lesson/exam?lesson_id={lesson_id}")
        time.sleep(3)
        self.address_dialogs(driver)

        q_lis = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
        if not q_lis:
            print("  未找到题目!", flush=True)
            return None

        total = len(q_lis)
        print(f"  共 {total} 道题", flush=True)

        stats = {"radio": 0, "checkbox": 0, "judge": 0, "answered": 0}

        for idx in range(total):
            if idx > 0:
                try:
                    driver.find_element(By.ID, "next_question").click()
                except Exception:
                    try:
                        q_lis_now = driver.find_elements(By.CSS_SELECTOR, ".exam_ul li")
                        if idx < len(q_lis_now):
                            driver.execute_script("arguments[0].click();", q_lis_now[idx])
                    except Exception:
                        pass
                time.sleep(0.8)

            self.address_dialogs(driver)

            try:
                h2 = driver.find_element(By.CSS_SELECTOR, ".exam_h2")
                question = h2.text.strip()
            except Exception:
                continue

            inputs = driver.find_elements(
                By.CSS_SELECTOR,
                ".answer_list input[type='radio'], .answer_list input[type='checkbox'], "
                ".answer_list_box input[type='radio'], .answer_list_box input[type='checkbox']")
            if not inputs:
                continue

            itype = "radio"
            if inputs[0].get_attribute("type") == "checkbox":
                itype = "checkbox"
            elif len(inputs) == 2:
                itype = "judge"

            options = []
            for inp in inputs:
                try:
                    options.append(inp.find_element(By.XPATH, "..").text.strip())
                except Exception:
                    options.append("")

            stats[itype] += 1
            prefix = {"radio": "[单选]", "checkbox": "[多选]", "judge": "[判断]"}

            answer = self.ask_ai(itype, question, options)
            print(f"    [{idx+1:2d}/{total}] {prefix[itype]} {question[:45]}... => {answer}", flush=True)

            inputs2 = driver.find_elements(
                By.CSS_SELECTOR,
                ".answer_list input[type='radio'], .answer_list input[type='checkbox'], "
                ".answer_list_box input[type='radio'], .answer_list_box input[type='checkbox']")

            if isinstance(answer, list):
                for a in answer:
                    if 1 <= a <= len(inputs2):
                        driver.execute_script("arguments[0].click();", inputs2[a - 1])
                        time.sleep(0.1)
            else:
                sel = max(1, min(answer, len(inputs2)))
                driver.execute_script("arguments[0].click();", inputs2[sel - 1])
                time.sleep(0.1)

            stats["answered"] += 1
            time.sleep(0.3)

        print(f"\n  答题统计: 单选{stats['radio']} 多选{stats['checkbox']} 判断{stats['judge']} 已答{stats['answered']}", flush=True)

        print("[*] 提交试卷...", flush=True)
        try:
            driver.find_element(By.ID, "submit_exam").click()
            time.sleep(2)
        except Exception as e:
            print(f"  点击交卷失败: {e}", flush=True)

        try:
            confirm = driver.find_element(By.CSS_SELECTOR, CSS_PUBLIC_SUBMIT)
            if confirm.is_displayed():
                confirm.click()
                time.sleep(1)
        except Exception:
            pass

        time.sleep(4)
        self.address_dialogs(driver)
        time.sleep(3)
        self.address_dialogs(driver)

        score = self._get_score(driver)
        return score

    def _get_score(self, driver):
        try:
            body = driver.page_source
            m = re.search(r'score_rate\d?["\']?\s*[>]\s*(\d+)\s*<', body)
            if m:
                score = int(m.group(1))
                print(f"\n  >>> 得分: {score} 分 <<<", flush=True)
                return score

            m = re.search(r'(\d+)\s*分', body)
            if m:
                score = int(m.group(1))
                print(f"\n  >>> 得分: {score} 分 <<<", flush=True)
                return score

            print("\n  未能获取分数", flush=True)
            idx = body.rfind("得分")
            if idx > 0:
                snippet = body[max(0,idx-130):idx+130]
                print(f"  页面片段: ...{snippet}...", flush=True)
            return None
        except Exception as e:
            print(f"  获取分数失败: {e}", flush=True)
            return None


def main():
    print("=" * 60, flush=True)
    print("  DeepSeek 自动答题测试", flush=True)
    print("=" * 60, flush=True)

    tester = ExamTester()
    driver = tester.start_browser()
    try:
        tester.login(driver)

        score1 = tester.run_exam(driver, lesson_id=567, course_name="第一课")
        if score1 is None:
            print("\n[FAIL] 未能获取第一课分数", flush=True)
        elif score1 >= 75:
            print(f"\n[PASS] 第一课 {score1}分，继续第二课...", flush=True)
            score2 = tester.run_exam(driver, lesson_id=568, course_name="第二课")
            if score2 is not None:
                print(f"\n第二课得分: {score2} 分", flush=True)
        else:
            print(f"\n[FAIL] 第一课 {score1}分，不继续", flush=True)

        print("\n浏览器保持打开60秒供检查...", flush=True)
        time.sleep(60)
    finally:
        driver.quit()
        print("浏览器已关闭", flush=True)


if __name__ == "__main__":
    main()
