from playwright.sync_api import sync_playwright
import time
import os

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1600, 'height': 900})
    page = context.new_page()

    print("Navigating to login page...")
    target_url = "http://localhost:3000/signin" # According to metadata the user is on 3000 so the original port works.
    page.goto("http://localhost:3000", wait_until="networkidle")

    print(f"Current URL: {page.url}")
    
    # Simple strategy: try finding inputs and fill them if they exist
    inputs = page.query_selector_all("input")
    if len(inputs) >= 2:
        print("Filling credentials...")
        inputs[0].fill("admin")
        inputs[1].fill("KavinAdmin@2026")
        page.keyboard.press("Enter")
        time.sleep(3)
        page.wait_for_load_state("networkidle")
        
    print("Capturing Dashboard...")
    # wait to ensure data loads
    time.sleep(3)
    dash_path = os.path.join(os.getcwd(), 'tmp', 'dashboard_real.png')
    if not os.path.exists(os.path.join(os.getcwd(), 'tmp')):
        os.makedirs(os.path.join(os.getcwd(), 'tmp'))
    page.screenshot(path=dash_path)
    print(f"Dashboard captured to {dash_path}")
    
    print("Navigating to Chat...")
    page.goto("http://localhost:3000/chat", wait_until="networkidle")
    time.sleep(2)
    chat_path = os.path.join(os.getcwd(), 'tmp', 'chat_real.png')
    page.screenshot(path=chat_path)
    print(f"Chat captured to {chat_path}")

    print("Navigating to Upload/Projects...")
    page.goto("http://localhost:3000/projects", wait_until="networkidle")
    time.sleep(2)
    projects_path = os.path.join(os.getcwd(), 'tmp', 'projects_real.png')
    page.screenshot(path=projects_path)
    print(f"Projects captured to {projects_path}")

    context.close()
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
