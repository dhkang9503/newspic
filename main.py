import os
import sys
import platform
import pickle
import time
import logging
import tempfile
from pathlib import Path

import requests
import pyperclip

from PIL import Image
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.blocking import BlockingScheduler

# -----------------------
# Config
# -----------------------
THREADS_COOKIE_FILE = "threads_cookies.pkl"
NEWSPIC_COOKIE_FILE = "newspic_cookies.pkl"

# 환경변수로 제어 가능 (기본 True: 서버는 보통 헤드리스)
USE_HEADLESS = os.getenv("USE_HEADLESS", "true").lower() == "true"

# 타임존
SCHED_TZ = "Asia/Seoul"

# 로깅
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

BASE_DIR = Path(__file__).resolve().parent


def is_linux():
    return platform.system().lower() == "linux"


def build_chrome():
    options = webdriver.ChromeOptions()
    # 서버/컨테이너 안정 플래그
    if USE_HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1366,768")
    # 필요 시 UA 지정
    # options.add_argument("user-agent=Mozilla/5.0 ...")

    # 다운로드 캐시가 느리면 WDM 캐시 경로 지정도 가능
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )
    return driver


class Browser:
    def __init__(self, cookie_file, url):
        self.cookie_file = str(BASE_DIR / cookie_file)
        self.url = url
        self.driver = None
        self.get_driver()

    def get_driver(self):
        if not self.driver:
            self.driver = build_chrome()
        return self.driver

    def _wait(self, condition, timeout=15):
        return WebDriverWait(self.driver, timeout).until(condition)

    def open_browser(self):
        """
        - 쿠키가 있으면: url 오픈 → 쿠키 로드 → 새로고침
        - 없으면:
            * 헤드리스인 경우: 로그인 불가하니 예외 안내
            * 헤드리스가 아니면: 수동 로그인 유도 후 쿠키 저장
        """
        self.driver.get(self.url)
        time.sleep(2)

        if not os.path.exists(self.cookie_file):
            if USE_HEADLESS:
                raise RuntimeError(
                    f"[cookie] '{self.cookie_file}' 가 없고 헤드리스 모드입니다. "
                    "헤드리스에서는 수동 로그인을 진행할 수 없습니다. "
                    "로컬(비헤드리스)에서 먼저 로그인 후 쿠키 파일을 서버에 업로드하세요."
                )
            else:
                self.login_and_save_session()
        else:
            try:
                self.load_cookies()
                self.driver.refresh()
                time.sleep(3)
                logging.info("✅ 쿠키 로드 후 새로고침 완료")
            except Exception as e:
                logging.warning(f"[cookie] 로드 실패 → 재로그인 시도: {e}")
                if USE_HEADLESS:
                    raise RuntimeError(
                        "헤드리스 모드에서 쿠키 로드에 실패했습니다. "
                        "로컬에서 쿠키를 갱신해 서버에 다시 배포하세요."
                    )
                self.login_and_save_session()

    def login_and_save_session(self):
        self.driver.get(self.url)
        print("🔐 브라우저에서 로그인한 뒤 Enter를 누르세요...")
        input("▶ 로그인 완료 후 Enter: ")
        self.save_cookies()

    def save_cookies(self):
        with open(self.cookie_file, "wb") as file:
            pickle.dump(self.driver.get_cookies(), file)
        logging.info("✅ 쿠키 저장 완료: %s", self.cookie_file)

    def load_cookies(self):
        with open(self.cookie_file, "rb") as file:
            cookies = pickle.load(file)
            for cookie in cookies:
                # selenium 4에서 'sameSite' 문제 회피
                if "sameSite" in cookie:
                    del cookie["sameSite"]
                # 유효 도메인에서만 add_cookie 가능 -> 이미 self.url 로 접속된 상태
                try:
                    self.driver.add_cookie(cookie)
                except Exception as e:
                    logging.debug(f"[cookie] add_cookie 실패: {e}")
        logging.info("✅ 쿠키 불러오기 완료")

    def switch_tab(self, url_keyword, timeout=10):
        """
        기존 로직 유지(절대 XPATH 그대로 쓰기 위함).
        탭 전환 안정화: 일정 시간 안에 keyword로 시작하는 URL 탭 전환.
        """
        end = time.time() + timeout
        while time.time() < end:
            for handle in self.driver.window_handles:
                self.driver.switch_to.window(handle)
                time.sleep(0.5)
                try:
                    cur = self.driver.current_url
                except Exception:
                    continue
                if cur.startswith(url_keyword):
                    time.sleep(0.8)
                    return True
            time.sleep(0.4)
        raise TimeoutException(f"[tab] '{url_keyword}' 로 시작하는 탭을 찾지 못했습니다.")

    def find(self, by, element, sleep=0.0, timeout=15, clickable=False):
        """
        기존 인터페이스 유지하되 내부는 WebDriverWait 기반.
        - 절대 XPATH 그대로 사용 가능
        - clickable=True면 클릭 가능 상태까지 대기
        """
        try:
            if clickable:
                el = self._wait(EC.element_to_be_clickable((by, element)), timeout=timeout)
            else:
                el = self._wait(EC.presence_of_element_located((by, element)), timeout=timeout)
            if sleep > 0:
                time.sleep(sleep)
            return el
        except TimeoutException:
            logging.warning(f"[find] timeout: {by} {element}")
            return None
        except Exception as e:
            logging.warning(f"[find] error: {e}")
            return None

    def close_other_tabs(self):
        """
        현재 탭을 기준으로 나머지 탭 정리 (원래 로직 유지)
        """
        current_url = self.driver.current_url
        current_handle = ""

        for handle in list(self.driver.window_handles):
            self.driver.switch_to.window(handle)
            time.sleep(0.2)
            try:
                if self.driver.current_url != current_url:
                    self.driver.close()
                    time.sleep(0.2)
                else:
                    current_handle = handle
            except Exception:
                # 이미 닫혔거나 접근 불가
                pass

        if current_handle:
            self.driver.switch_to.window(current_handle)

    def terminate(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None


def safe_get_article_url(driver):
    """
    pyperclip(클립보드)이 없는 서버 대비:
    canonical / og:url 메타 우선 사용, 실패 시 현재 URL
    """
    try:
        el = driver.find_element(By.XPATH, "//link[@rel='canonical']")
        href = el.get_attribute("href")
        if href:
            return href
    except Exception:
        pass
    try:
        el = driver.find_element(By.XPATH, "//meta[@property='og:url']")
        href = el.get_attribute("content")
        if href:
            return href
    except Exception:
        pass
    try:
        return driver.current_url
    except Exception:
        return ""


def download_and_crop(image_url, out_path, crop_height=360, timeout=10):
    """
    이미지 다운로드 + 상단 crop, 임시파일 정리
    """
    if not image_url:
        return False
    try:
        r = requests.get(image_url, timeout=timeout)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tf:
            tf.write(r.content)
            tmp = tf.name
        with Image.open(tmp) as img:
            w, h = img.size
            box = (0, 0, w, min(h, crop_height))
            img.crop(box).save(out_path, format="JPEG")
        os.remove(tmp)
        return True
    except Exception as e:
        logging.warning(f"[image] {e}")
        return False


def crawl_newspic_ai_contents():
    start_time = datetime.now().strftime("%m%d%H%M")
    newspic_browser = Browser(NEWSPIC_COOKIE_FILE, "https://partners.newspic.kr/")

    newspic_browser.open_browser()

    # 전체 페이지 수
    total_pages_el = newspic_browser.find(By.XPATH, "/html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[1]/div/p/em[3]")
    if not total_pages_el:
        raise RuntimeError("전체 페이지 수를 가져오지 못했습니다.")
    total_pages = int(total_pages_el.text.strip())

    news_info = {}

    for page in range(total_pages):
        for news_index in range(3):
            try:
                el = newspic_browser.find(
                    By.XPATH,
                    f"/html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[2]/ul/li[{news_index + 1}]/div[2]/a",
                    clickable=True,
                )
                if el:
                    el.click()
                else:
                    logging.info("기사 링크 요소를 찾지 못해 건너뜁니다.")
                    continue
            except Exception as e:
                logging.info(f"기사 클릭 예외: {e}")
                continue

            unique_id = f"{start_time}_{page + 1}_{news_index + 1}"

            # 기사 탭으로 전환
            newspic_browser.switch_tab("https://m.newspic.kr/view.html")

            # 뉴스 타이틀
            title_el = newspic_browser.find(By.XPATH, "/html/body/div[2]/div[1]/div[1]/div[2]/h3")
            if not title_el:
                logging.info("제목을 찾지 못해 건너뜁니다.")
                newspic_browser.close_other_tabs()
                continue
            news_title = title_el.text.strip()

            # 링크 복사 버튼 클릭 후 pyperclip, 실패 시 fallback
            news_url = ""
            try:
                share_btn = newspic_browser.find(By.XPATH, "/html/body/div[3]/div[2]/ul/li[1]/button", clickable=True)
                if share_btn:
                    share_btn.click()
                    time.sleep(0.8)
                    try:
                        news_url = pyperclip.paste().strip()
                    except Exception:
                        news_url = ""
                if not news_url:
                    news_url = safe_get_article_url(newspic_browser.driver)
            except Exception:
                news_url = safe_get_article_url(newspic_browser.driver)

            # 이미지들
            img_elements = newspic_browser.driver.find_elements(
                By.XPATH, "/html/body/div[2]/div[1]/div[1]/div[5]//img"
            )
            images = []
            for e in img_elements:
                try:
                    src = e.get_attribute("src") or ""
                    if src.startswith("https://images-cdn.newspic.kr"):
                        images.append(src)
                except Exception:
                    pass

            image_path = str(BASE_DIR / f"{unique_id}.jpg")
            if images:
                ok = download_and_crop(images[0], image_path)
                if not ok:
                    logging.info("이미지 다운로드 실패 → 이미지 없이 진행")
                    image_path = ""
            else:
                logging.info("이미지 없음 → 이미지 없이 진행")
                image_path = ""

            # 파트너스로 복귀 및 탭 정리
            newspic_browser.switch_tab("https://partners.newspic.kr")
            newspic_browser.close_other_tabs()

            news_info[unique_id] = (news_title, news_url, image_path)

        # next 버튼
        next_btn = newspic_browser.find(By.XPATH, "/html/body/div[6]/main/div[1]/section[2]/div[2]/div/p/button[2]", clickable=True)
        if next_btn:
            next_btn.click()
            time.sleep(1)
        else:
            logging.info("다음 버튼을 찾지 못했습니다. 페이지 루프 종료.")
            break

    newspic_browser.terminate()
    return news_info


def upload_news_into_threads(news_info):
    try:
        threads_browser = Browser(THREADS_COOKIE_FILE, "https://threads.net/")
        threads_browser.open_browser()
        time.sleep(3)  # 초기 렌더 안정화

        for key, (title, url, image) in news_info.items():
            # 게시버튼 클릭
            e = threads_browser.find(
                By.XPATH,
                "/html/body/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div[1]/div[1]/div/div/div[2]/div[1]/div[2]/div/div[2]/div",
                clickable=True,
            )
            if not e:
                logging.info("게시 시작 버튼을 찾지 못해 건너뜁니다.")
                continue
            e.click()
            time.sleep(0.5)

            # 제목 입력
            e = threads_browser.find(
                By.XPATH,
                "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[3]/div/div/div[1]/div[2]/div[2]/div[1]/p",
            )
            if not e:
                logging.info("본문 입력창을 찾지 못해 건너뜁니다.")
                continue
            e.send_keys(title)
            time.sleep(0.3)

            # 댓글 클릭
            e = threads_browser.find(
                By.XPATH,
                "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[3]/div/div/div[2]/div[2]/span",
                clickable=True,
            )
            if e:
                e.click()
                time.sleep(0.3)

            # 댓글에 주소 입력
            e = threads_browser.find(
                By.XPATH,
                "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[3]/div[2]/div/div[1]/div[2]/div[2]/div[1]/p",
            )
            if e and url:
                e.send_keys(f"내용 이어서 보기: {url}")
                time.sleep(0.3)

            # 이미지 추가(있을 때만)
            if image:
                file_input = threads_browser.find(By.XPATH, '//input[@type="file"]')
                if file_input:
                    image_path = str(Path(image).resolve())
                    file_input.send_keys(image_path)
                    time.sleep(1.0)

            # 게시 버튼 클릭
            e = threads_browser.find(
                By.XPATH,
                "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[4]/div/div[1]/div",
                clickable=True,
            )
            if e:
                e.click()
                time.sleep(3.0)  # 업로드 대기
            else:
                logging.info("최종 게시 버튼을 찾지 못했습니다. 이 항목을 건너뜁니다.")

    except Exception as e:
        logging.error(f"[threads] 업로드 중 예외: {e}")
        try:
            threads_browser.terminate()
        except Exception:
            pass
        return False

    threads_browser.terminate()
    return True


def main():
    try:
        news_info = crawl_newspic_ai_contents()
        print(f"크롤링된 뉴스 개수: {len(news_info)}")
        upload_news_into_threads(news_info)
    except Exception as e:
        logging.warning(f"[main] 1차 시도 실패 → 재시도: {e}")
        time.sleep(3)
        news_info = crawl_newspic_ai_contents()
        upload_news_into_threads(news_info)


if __name__ == "__main__":
    main()
    # scheduler = BlockingScheduler(timezone=SCHED_TZ)
    # # 중복 실행 방지, 미스파이어 허용
    # scheduler.add_job(
    #     main,
    #     CronTrigger(minute=25, second=0),
    #     max_instances=1,
    #     coalesce=True,
    #     misfire_grace_time=120,
    # )
    # logging.info("⏰ 스케줄러 시작 (매시 25분 00초)")
    # scheduler.start()
