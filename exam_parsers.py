from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

TASK_BASE_URL = "https://dxpx.uestc.edu.cn"
COMPREHENSIVE_PRIMARY_SCORE = 80
COMPREHENSIVE_FALLBACK_SCORE = 60
COMPREHENSIVE_PRIMARY_ATTEMPTS = 3

_TASK_STATUS_MARKERS = (
    "未被授权",
    "未授权",
    "已完成",
    "已通过",
    "未进行",
    "继续考试",
    "继续答题",
    "开始考试",
    "开始测试",
    "去测试",
    "未完成",
    "进行中",
)
_TASK_COMPLETED_MARKERS = ("已完成", "已通过")
_TASK_UNAUTHORIZED_MARKERS = ("未被授权", "未授权", "暂无权限", "未开放")
_TASK_CTA_MARKERS = (
    "去测试",
    "开始测试",
    "开始考试",
    "继续考试",
    "继续答题",
    "恢复考试",
    "去答题",
)


@dataclass(frozen=True)
class TaskPageState:
    theory_completed: bool
    theory_status_text: str
    comprehensive_status_text: str
    comprehensive_cta_href: str
    comprehensive_cta_text: str
    comprehensive_completed: bool


def normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def is_completed_status(text: str) -> bool:
    normalized = normalize_text(text)
    return any(marker in normalized for marker in _TASK_COMPLETED_MARKERS)


def is_unauthorized_status(text: str) -> bool:
    normalized = normalize_text(text)
    return any(marker in normalized for marker in _TASK_UNAUTHORIZED_MARKERS)


def get_comprehensive_required_score(attempt_no: int) -> int:
    normalized_attempt = max(1, attempt_no)
    if normalized_attempt <= COMPREHENSIVE_PRIMARY_ATTEMPTS:
        return COMPREHENSIVE_PRIMARY_SCORE
    return COMPREHENSIVE_FALLBACK_SCORE


def is_comprehensive_score_accepted(score: int | None, attempt_no: int) -> bool:
    if score is None:
        return False
    if score >= COMPREHENSIVE_PRIMARY_SCORE:
        return True
    if attempt_no >= COMPREHENSIVE_PRIMARY_ATTEMPTS and score >= COMPREHENSIVE_FALLBACK_SCORE:
        return True
    return False


def detect_comprehensive_question_type(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    if soup.select(".answer_list_box input.summary_question"):
        return "fill_blank"

    if soup.select(".answer_list_box input[type='checkbox']"):
        return "multi"

    radio_inputs = soup.select(
        ".answer_list input[type='radio'], .answer_list_box input[type='radio']"
    )
    if len(radio_inputs) == 2:
        return "judge"
    if len(radio_inputs) > 0:
        return "single"
    return None


def parse_task_page_html(html: str, base_url: str = TASK_BASE_URL) -> TaskPageState:
    soup = BeautifulSoup(html or "", "html.parser")
    theory_block = _find_task_block(soup, "理论学习")
    comprehensive_block = _find_task_block(soup, "综合测试")

    theory_status_text = _extract_status_text(theory_block, "理论学习")
    comprehensive_status_text = _extract_status_text(comprehensive_block, "综合测试")
    comprehensive_cta_href, comprehensive_cta_text = _extract_cta(
        comprehensive_block,
        base_url,
    )

    comprehensive_text = _block_text(comprehensive_block)
    comprehensive_completed = (
        is_completed_status(comprehensive_status_text or comprehensive_text)
        and not any(marker in comprehensive_cta_text for marker in _TASK_CTA_MARKERS)
    )

    return TaskPageState(
        theory_completed=is_completed_status(theory_status_text or _block_text(theory_block)),
        theory_status_text=theory_status_text,
        comprehensive_status_text=comprehensive_status_text,
        comprehensive_cta_href=comprehensive_cta_href,
        comprehensive_cta_text=comprehensive_cta_text,
        comprehensive_completed=comprehensive_completed,
    )


def _find_task_block(soup: BeautifulSoup, label: str) -> Tag | None:
    candidates: list[tuple[int, int, Tag]] = []
    seen: set[int] = set()

    for text_node in soup.find_all(string=True):
        if label not in normalize_text(str(text_node)):
            continue

        parent = text_node.parent
        if not isinstance(parent, Tag):
            continue

        current: Tag | None = parent
        depth = 0
        while isinstance(current, Tag) and depth < 6:
            if current.name in {"html", "body"}:
                break
            if current.name in {"a", "button"}:
                parent_tag = current.parent
                current = parent_tag if isinstance(parent_tag, Tag) else None
                depth += 1
                continue

            block_text = _block_text(current)
            if label in block_text and len(block_text) <= 360:
                key = id(current)
                if key not in seen:
                    seen.add(key)
                    candidates.append((_score_block(current, label), depth, current))

            parent_tag = current.parent
            current = parent_tag if isinstance(parent_tag, Tag) else None
            depth += 1

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return candidates[0][2]


def _score_block(block: Tag, label: str) -> int:
    text = _block_text(block)
    score = 0

    if _has_heading_like(block, label):
        score += 80
    if block.name not in {"a", "button", "span"}:
        score += 30
    if any(marker in text for marker in _TASK_STATUS_MARKERS):
        score += 100
    if block.select("a[href], button"):
        score += 60

    score -= len(text) // 12
    return score


def _has_heading_like(block: Tag, label: str) -> bool:
    if normalize_text(block.get_text(" ", strip=True)) == label:
        return True

    for child in block.find_all(
        ["h1", "h2", "h3", "h4", "strong", "span", "p", "dt", "dd", "div"],
        recursive=False,
    ):
        if normalize_text(child.get_text(" ", strip=True)) == label:
            return True
    return False


def _block_text(block: Tag | None) -> str:
    if block is None:
        return ""
    return normalize_text(block.get_text(" ", strip=True))


def _extract_status_text(block: Tag | None, label: str) -> str:
    text = _block_status_text(block)
    if not text:
        return ""

    for marker in _TASK_STATUS_MARKERS:
        if marker in text:
            return marker

    remainder = text.replace(label, "").strip(" :：-")
    return remainder


def _extract_cta(block: Tag | None, base_url: str) -> tuple[str, str]:
    if block is None:
        return "", ""

    best_href = ""
    best_text = ""
    best_score = -1

    for link in block.select("a[href]"):
        href = (link.get("href") or "").strip()
        text = normalize_text(link.get_text(" ", strip=True))
        if not href and not text:
            continue

        score = 0
        if "/jjfz/exam_center/end_exam" in href:
            score += 200
        if text in _TASK_CTA_MARKERS:
            score += 100
        elif any(marker in text for marker in _TASK_CTA_MARKERS):
            score += 60
        if href and not href.lower().startswith("javascript"):
            score += 20

        if score > best_score:
            best_score = score
            best_href = urljoin(base_url, href) if href else ""
            best_text = text

    return best_href, best_text


def _block_status_text(block: Tag | None) -> str:
    if block is None:
        return ""

    parts = []
    for text_node in block.stripped_strings:
        parent = getattr(text_node, "parent", None)
        if isinstance(parent, Tag):
            if parent.name in {"a", "button"}:
                continue
            if parent.find_parent(["a", "button"]) is not None:
                continue
        parts.append(normalize_text(str(text_node)))
    return normalize_text(" ".join(parts))
