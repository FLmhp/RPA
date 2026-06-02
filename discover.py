import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import ddddocr
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://dxpx.uestc.edu.cn"
USERNAME = "2024090906010"
PASSWORD = "kD-7VXkCAGt7AEE"
OUTPUT_DIR = Path(__file__).parent / "discover_output"

PAGES_DIR = OUTPUT_DIR / "pages"
JS_CHUNKS_DIR = OUTPUT_DIR / "js_chunks"
API_RESPONSES_DIR = OUTPUT_DIR / "api_responses"
COURSES_DIR = API_RESPONSES_DIR / "courses"


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def setup_output_dirs() -> None:
    for d in [PAGES_DIR, JS_CHUNKS_DIR, API_RESPONSES_DIR, COURSES_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/login/",
        "Accept": "application/json, text/plain, */*",
    })
    return session


def login(session: requests.Session) -> dict[str, Any]:
    print("[*] Initializing ddddocr...")
    ocr = ddddocr.DdddOcr(show_ad=False)
    print("[*] ddddocr ready.")

    print("[*] Visiting login page...")
    session.get(f"{BASE_URL}/login/", timeout=15)

    hashed_pass = sha1_hex(PASSWORD)
    access_token = None

    for attempt in range(1, 11):
        resp = session.get(f"{BASE_URL}/api/v1/public/captcha?={time.time()}", timeout=15)
        result = ocr.classification(resp.content)
        captcha = re.sub(r"[^a-zA-Z0-9]", "", result)

        if len(captcha) != 5:
            print(f"  Attempt {attempt}: OCR got '{result}' -> '{captcha}' (wrong length), retry...")
            continue

        resp = session.post(
            f"{BASE_URL}/api/v1/public/login",
            data={"u_name": USERNAME, "u_pass": hashed_pass, "v_code": captcha, "next_url": "", "app_id": ""},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=15,
        )
        payload = resp.json()
        if int(payload.get("code", 0)) == 1:
            access_token = payload.get("access_token", "")
            print(f"  Login success on attempt {attempt}, captcha='{captcha}'")
            break
        print(f"  Attempt {attempt}: captcha='{captcha}', server msg='{payload.get('message','')}', retry...")

    if not access_token:
        print("[-] Login failed after 10 attempts.")
        sys.exit(1)

    session.headers.update({"Authorization": f"Bearer {access_token}", "user-token": access_token})
    return payload


def fetch_page(session: requests.Session, url: str, filename: str) -> str | None:
    full_url = url if url.startswith("http") else f"{BASE_URL}{url}"
    print(f"  GET {url}")
    try:
        resp = session.get(full_url, timeout=30, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"    FAILED: {e}")
        return None

    filepath = PAGES_DIR / filename
    content = resp.text
    filepath.write_text(content, encoding="utf-8")
    print(f"    -> {filepath} ({len(content)} chars, status={resp.status_code})")

    if resp.url != full_url:
        print(f"    Redirected to: {resp.url}")

    return content


def extract_js_links(html: str, base_url: str) -> list[tuple[str, str]]:
    scripts = []
    for m in re.finditer(r'<script[^>]+src="([^"]+)"', html):
        src = m.group(1)
        full_url = urljoin(base_url, src)
        name = os.path.basename(src.split("?")[0])
        scripts.append((full_url, name))
    return scripts


def download_js_chunks(session: requests.Session, scripts: list[tuple[str, str]]) -> list[Path]:
    saved = []
    for url, name in scripts:
        if "/login/static/" in url:
            continue
        print(f"  GET JS: {url}")
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"    FAILED: {e}")
            continue

        filepath = JS_CHUNKS_DIR / name
        content = resp.text
        filepath.write_text(content, encoding="utf-8")
        print(f"    -> {filepath} ({len(content)} chars)")
        saved.append(filepath)
    return saved


def probe_api(session: requests.Session, method: str, url: str, filename: str | None = None) -> dict | None:
    full_url = url if url.startswith("http") else f"{BASE_URL}{url}"
    print(f"  {method} {url}")
    try:
        if method == "GET":
            resp = session.get(full_url, timeout=30)
        else:
            resp = session.post(full_url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"    -> FAILED ({e})")
        return None

    try:
        data = resp.json()
    except Exception:
        data = {"_raw": resp.text[:500], "_status": resp.status_code}

    if filename:
        filepath = API_RESPONSES_DIR / filename
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    -> {filepath} ({len(json.dumps(data))} bytes, status={resp.status_code})")
    else:
        print(f"    -> OK (status={resp.status_code}, {len(json.dumps(data))} bytes)")

    return data


def grep_js_for_patterns(js_files: list[Path], patterns: dict[str, str]) -> dict[str, list[tuple[str, int, str]]]:
    results: dict[str, list[tuple[str, int, str]]] = {}
    for label, regex in patterns.items():
        results[label] = []
        for fpath in js_files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in re.finditer(regex, content):
                line_no = content[:m.start()].count("\n") + 1
                line = content.split("\n")[line_no - 1].strip()[:200]
                results[label].append((fpath.name, line_no, line))
    return results


def deep_probe_courses(session: requests.Session, course_list: list[dict]) -> dict:
    results = {}
    for course in course_list:
        cid = course.get("id") or course.get("lesson_id") or course.get("course_id")
        cname = course.get("title") or course.get("name") or course.get("lesson_name") or f"course_{cid}"
        if not cid:
            continue
        print(f"\n  === Deep probe: {cname} (id={cid}) ===")

        for suffix in ["", "/section", "/video", "/record", "/info", "/detail"]:
            url = f"/api/v1/jjfz/lesson/{cid}{suffix}"
            data = probe_api(session, "GET", url, f"courses/{cid}{suffix}.json")
            if data and not (isinstance(data, dict) and data.get("code") in ("0", 0, -1)):
                key = f"{cid}{suffix}"
                results[key] = data
            time.sleep(0.3)
        time.sleep(0.5)
    return results


def print_summary(
    login_data: dict,
    page_results: dict,
    api_results: dict,
    js_grep_results: dict,
    course_data: dict,
) -> None:
    print("\n" + "=" * 70)
    print("=== DISCOVERY SUMMARY ===")
    print("=" * 70)

    print(f"\nLogin:  user_name={login_data.get('access_token','?')[:20]}...")

    print("\nPages fetched:")
    for url, ok in page_results.items():
        print(f"  {'OK' if ok else 'FAIL'}  {url}")

    print("\nAPI probes:")
    print(f"{'Method':6} {'URL':55} {'Status'}")
    print("-" * 70)
    for (method, url), result in api_results.items():
        status = "OK" if result is not None else "FAIL"
        print(f"{method:6} {url:55} {status}")

    print("\nJS keyword grep results:")
    for label, matches in js_grep_results.items():
        print(f"\n  [{label}] - {len(matches)} matches:")
        for fname, line_no, line in matches[:5]:
            print(f"    {fname}:{line_no}  {line[:120]}")

    print(f"\nCourse deep probes: {len(course_data)} endpoints probed")
    print(f"\nAll artifacts saved to: {OUTPUT_DIR}")


def main() -> None:
    setup_output_dirs()
    session = create_session()

    print("=" * 70)
    print("Phase 1: Login")
    print("=" * 70)
    login_data = login(session)
    print(f"  logged in, token length={len(login_data.get('access_token',''))}")

    print("\n" + "=" * 70)
    print("Phase 2: Fetch SPA shell pages")
    print("=" * 70)
    page_results: dict[str, bool] = {}

    html = fetch_page(session, "/user/", "user.html")
    page_results["/user/"] = html is not None

    html = fetch_page(session, "/jjfz/lesson", "lesson.html")
    page_results["/jjfz/lesson"] = html is not None

    print("\n" + "=" * 70)
    print("Phase 3: Extract & download JS chunks")
    print("=" * 70)
    all_scripts: list[tuple[str, str]] = []
    for filename in ["user.html", "lesson.html"]:
        filepath = PAGES_DIR / filename
        if filepath.exists():
            html = filepath.read_text(encoding="utf-8")
            scripts = extract_js_links(html, f"{BASE_URL}/")
            all_scripts.extend(scripts)

    all_scripts = list(dict.fromkeys(all_scripts))
    print(f"  Found {len(all_scripts)} unique JS script tags")
    js_files = download_js_chunks(session, all_scripts)

    print("\n" + "=" * 70)
    print("Phase 4: Probe API endpoints")
    print("=" * 70)
    api_results: dict[tuple[str, str], Any] = {}
    probes = [
        ("GET", "/api/v1/jjfz/lesson"),
        ("GET", "/api/v1/jjfz/lesson/list"),
        ("GET", "/api/v1/jjfz/lesson/index"),
        ("GET", "/api/v1/jjfz/lesson/category"),
        ("GET", "/api/v1/jjfz/course"),
        ("GET", "/api/v1/jjfz/course/list"),
        ("GET", "/api/v1/jjfz/course/index"),
        ("GET", "/api/v1/jjfz/category"),
        ("GET", "/api/v1/user/lesson"),
        ("GET", "/api/v1/user/course"),
        ("GET", "/api/v1/study/record"),
        ("GET", "/api/v1/study/progress"),
        ("GET", "/api/v1/lesson"),
        ("GET", "/api/v1/lesson/list"),
        ("GET", "/api/v1/course/list"),
        ("GET", "/api/v1/jjfz/lesson/record"),
        ("GET", "/api/v1/public/config"),
        ("GET", "/api/v1/user/info"),
    ]

    for method, url in probes:
        data = probe_api(session, method, url, f"{url.replace('/', '_')}.json")
        api_results[(method, url)] = data
        time.sleep(0.3)

    print("\n" + "=" * 70)
    print("Phase 5: Grep JS chunks for API patterns")
    print("=" * 70)
    grep_patterns = {
        "lesson_api": r"""(?:/api/v[12]/[a-z]+/lesson[^"'\s,;)]*)""",
        "course_api": r"""(?:/api/v[12]/[a-z]+/course[^"'\s,;)]*)""",
        "video_api": r"""(?:/api/v[12]/[a-z]+/video[^"'\s,;)]*)""",
        "record_api": r"""(?:/api/v[12]/[a-z]+/(?:record|progress|report)[^"'\s,;)]*)""",
        "section_chapter": r"""(?:(?:section|chapter)_?id|sectionId|chapterId)""",
        "video_player": r"""(?:video[Pp]layer|player|Aliplayer|TcPlayer|vodPlayer|jwplayer)""",
        "progress_tracking": r"""(?:watchTime|watch_time|played_time|playTime|duration|progress|学习进度)""",
        "completion_status": r"""(?:is_completed|is_finished|has_studied|finish|complete|已完成|已学习|学习完成)""",
        "start_learn": r"""(?:开始学习|继续学习|startLearn|startLearning|goStudy|goLearn)""",
        "ajax_headers": r"""headers\[["']([^"']+)["']\]""",
    }
    js_grep_results = grep_js_for_patterns(js_files, grep_patterns)

    for label, matches in js_grep_results.items():
        print(f"  [{label}]: {len(matches)} matches")
        for fname, line_no, line in matches[:3]:
            print(f"    {fname}:{line_no}  {line[:100]}")

    print("\n" + "=" * 70)
    print("Phase 6: Deep probe courses")
    print("=" * 70)
    course_data: dict = {}
    lesson_list_data = api_results.get(("GET", "/api/v1/jjfz/lesson"))
    if lesson_list_data and isinstance(lesson_list_data, dict):
        lessons = lesson_list_data.get("data") or []
        if not isinstance(lessons, list):
            lessons = []
        print(f"  Found {len(lessons)} courses in /api/v1/jjfz/lesson")
        course_data = deep_probe_courses(session, lessons)
    else:
        alt_urls = [
            ("GET", "/api/v1/jjfz/lesson/list"),
            ("GET", "/api/v1/user/lesson"),
            ("GET", "/api/v1/user/course"),
        ]
        found = False
        for method, url in alt_urls:
            data = api_results.get((method, url))
            if data and isinstance(data, dict):
                courses = data.get("data") or []
                if isinstance(courses, list) and courses:
                    print(f"  Found {len(courses)} courses in {url}")
                    course_data = deep_probe_courses(session, courses)
                    found = True
                    break
        if not found:
            print("  No course list found in any probed endpoint")

    print("\n" + "=" * 70)
    print("Phase 7: Save session state")
    print("=" * 70)
    session_info = {
        "cookies": {k: v for k, v in session.cookies.items()},
        "token": login_data.get("access_token", ""),
        "login_data": {k: v for k, v in login_data.items() if k not in ("access_token",)},
    }
    (OUTPUT_DIR / "session.json").write_text(
        json.dumps(session_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Saved session to {OUTPUT_DIR / 'session.json'}")

    grep_file = OUTPUT_DIR / "js_grep_results.txt"
    with grep_file.open("w", encoding="utf-8") as f:
        for label, matches in js_grep_results.items():
            f.write(f"\n=== {label} ({len(matches)} matches) ===\n")
            for fname, line_no, line in matches:
                f.write(f"  {fname}:{line_no}  {line}\n")
    print(f"  Saved JS grep results to {grep_file}")

    endpoints_file = OUTPUT_DIR / "endpoints.txt"
    with endpoints_file.open("w", encoding="utf-8") as f:
        for (method, url), result in api_results.items():
            status = "OK" if result is not None else "FAIL"
            f.write(f"{method:4} {url:55} {status}\n")
    print(f"  Saved endpoint list to {endpoints_file}")

    print_summary(login_data, page_results, api_results, js_grep_results, course_data)
    print(f"\n[DONE] All artifacts saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
