import os
import pickle
import time
import requests
import pyperclip

from PIL import Image
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.blocking import BlockingScheduler

THREADS_COOKIE_FILE = "threads_cookies.pkl"
NEWSPIC_COOKIE_FILE = "newspic_cookies.pkl"

class Browser:
    def __init__(self, cookie_file, url):
        self.cookie_file = cookie_file
        self.url = url
        self.driver = None
        self.get_driver()

    def get_driver(self):
        if not self.driver:
            options = webdriver.ChromeOptions()
            # options.add_argument("--start-maximized")
            # options.add_argument("--headless")  # 백그라운드 실행 시 활성화
            self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

        return self.driver

    def open_browser(self):
        if not os.path.exists(self.cookie_file):
            self.login_and_save_session()
        else:
            self.driver.get(self.url)
            time.sleep(3)
            self.load_cookies()
            self.driver.refresh()
            time.sleep(5)

    def login_and_save_session(self):
        self.driver.get(self.url)
        print("🔐 로그인 후 Enter 키를 누르세요...")
        input("▶ 로그인 완료 후 Enter")
        self.save_cookies()

    def save_cookies(self):
        with open(self.cookie_file, "wb") as file:
            pickle.dump(self.driver.get_cookies(), file)
        print("✅ 쿠키 저장 완료")

    def load_cookies(self):
        with open(self.cookie_file, "rb") as file:
            cookies = pickle.load(file)
            for cookie in cookies:
                # 도메인 지정 방지
                if "sameSite" in cookie:
                    del cookie["sameSite"]
                self.driver.add_cookie(cookie)
        print("✅ 쿠키 불러오기 완료")

    def switch_tab(self, url_keyword):
        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            time.sleep(1)
            if self.driver.current_url.startswith(url_keyword):
                time.sleep(1)
                break

    def find(self, by, element, sleep=0.5):
        try:
            ret = self.driver.find_element(by, element)
            time.sleep(sleep)
            return ret
        except:
            pass

        return None

    def close_other_tabs(self):
        current_url = self.driver.current_url
        current_handle = ''

        for handle in self.driver.window_handles:
            self.driver.switch_to.window(handle)
            time.sleep(0.4)
            if self.driver.current_url != current_url:
                self.driver.close()
                time.sleep(0.4)
            else:
                current_handle = handle

        self.driver.switch_to.window(current_handle)

    def terminate(self):
        if self.driver:
            self.driver.quit()

        self.driver = None

def crawl_newspic_ai_contents():
    start_time = datetime.now().strftime("%m%d%H%M")

    newspic_browser = Browser(NEWSPIC_COOKIE_FILE, "https://partners.newspic.kr/")

    newspic_browser.open_browser()

    # 전체 페이지 수
    total_pages = int(newspic_browser.find(By.XPATH, "/html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[1]/div/p/em[3]").text)

    news_info = dict()

    for page in range(total_pages):
        # /html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[2]/ul/li[1]/div[2]/a : 0
        # /html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[2]/ul/li[2]/div[2]/a : 1
        # /html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[2]/ul/li[3]/div[2]/a : 2

        for news_index in range(3):
            try:
                newspic_browser.find(By.XPATH, f"/html/body/div[6]/main/div[1]/section[2]/div[2]/div/div[2]/ul/li[{news_index + 1}]/div[2]/a").click()
            except:
                print('ex')

            unique_id = start_time + f"_{page + 1}_{news_index + 1}"

            # 기사 탭으로 전환
            newspic_browser.switch_tab("https://m.newspic.kr/view.html")

            # 뉴스 타이틀
            news_title = newspic_browser.find(By.XPATH, "/html/body/div[2]/div[1]/div[1]/div[2]/h3").text

            # 링크 복사
            newspic_browser.find(By.XPATH, "/html/body/div[3]/div[2]/ul/li[1]/button").click()
            time.sleep(1)
            news_url = pyperclip.paste()

            images = [e.get_attribute('src') for e in newspic_browser.driver.find_elements(By.XPATH, "/html/body/div[2]/div[1]/div[1]/div[5]//img") if e.get_attribute('src').startswith('https://images-cdn.newspic.kr')]
            image_url = images[0]

            # 이미지 다운로드
            res = requests.get(image_url)
            with open(f'{unique_id}_temp.jpg', 'wb') as f:
                f.write(res.content)

            img = Image.open(f'{unique_id}_temp.jpg')
            width, height = img.size
            cropped = img.crop((0, 0, width, min(height, 360)))
            cropped.save(f'{unique_id}.jpg')

            newspic_browser.switch_tab("https://partners.newspic.kr")
            newspic_browser.close_other_tabs()

            news_info[unique_id] = (news_title, news_url, f'{unique_id}.jpg')

        # next 버튼
        newspic_browser.find(By.XPATH, "/html/body/div[6]/main/div[1]/section[2]/div[2]/div/p/button[2]").click()
        time.sleep(1)

    newspic_browser.terminate()
    return news_info

def upload_news_into_threads(news_info):
    try:
        threads_browser = Browser(THREADS_COOKIE_FILE, "https://threads.net/")
        threads_browser.open_browser()
        time.sleep(10)

        for key, (title, url, image) in news_info.items():
            # 게시버튼 클릭
            e = threads_browser.find(By.XPATH, "/html/body/div[2]/div/div/div[2]/div[2]/div/div/div/div[1]/div[1]/div[1]/div/div/div[2]/div[1]/div[2]/div/div[2]/div")
            e.click()
            time.sleep(1)

            # 제목 입력
            e = threads_browser.find(By.XPATH, "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[2]/div/div/div[1]/div[2]/div[2]/div[1]/p")
            e.send_keys(title)
            time.sleep(1)

            # 댓글 클릭
            e = threads_browser.find(By.XPATH, "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[2]/div/div/div[2]/div[2]/span")
            e.click()
            time.sleep(1)

            # 댓글에 주소 입력
            e = threads_browser.find(By.XPATH, "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[2]/div[2]/div/div[1]/div[2]/div[2]/div[1]/p")
            e.send_keys(f"내용 이어서 보기: {url}")
            time.sleep(1)

            # 이미지 추가
            e = threads_browser.find(By.XPATH, '//input[@type="file"]')
            e.send_keys(f'C:\\Users\\Kang\\Desktop\\project\\threads-newspic\\{image}')
            time.sleep(1)

            # 게시 버튼 클릭
            e = threads_browser.find(By.XPATH, "/html/body/div[2]/div/div/div[3]/div/div/div[1]/div/div[2]/div/div/div/div[2]/div/div/div/div/div/div[3]/div/div[1]/div")
            e.click()
            time.sleep(10)
    except:
        threads_browser.terminate()
        return False

    threads_browser.terminate()
    return True

def main():
    try:
        news_info = crawl_newspic_ai_contents()
        upload_news_into_threads(news_info)
    except:
        news_info = crawl_newspic_ai_contents()
        upload_news_into_threads(news_info)

if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(main, CronTrigger(minute=25, second=0))
    scheduler.start()
