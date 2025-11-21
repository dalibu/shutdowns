"""
Manual test script to analyze CEK form behavior and find correct selectors.
Run this with: python test_cek_form.py
"""
import asyncio
from playwright.async_api import async_playwright

async def test_cek_form():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        # Navigate to group lookup page
        print("Navigating to CEK group lookup page...")
        await page.goto("https://cek.dp.ua/index.php/cpojivaham/pobutovi-spozhyvachi/viznachennya-chergy.html")
        await page.wait_for_timeout(3000)
        
        # Fill the form
        print("Filling form...")
        await page.fill('input#city', 'м. Дніпро')
        await page.wait_for_timeout(500)
        
        await page.fill('input#street', 'вул. Казака Мамая')
        await page.wait_for_timeout(500)
        
        await page.fill('input#house', '10')
        await page.wait_for_timeout(500)
        
        # Look for submit button
        print("Looking for submit button...")
        buttons = await page.locator('button, input[type="submit"], input[type="button"]').all()
        print(f"Found {len(buttons)} buttons")
        
        for i, btn in enumerate(buttons):
            text = await btn.inner_text() if await btn.evaluate('el => el.tagName') == 'BUTTON' else await btn.get_attribute('value')
            print(f"  Button {i}: {text}")
        
        # Try to find the result area before clicking
        print("\nLooking for result area...")
        result_elements = await page.locator('*').evaluate_all('''
            elements => elements
                .filter(el => el.textContent && el.textContent.toLowerCase().includes('черг'))
                .map(el => ({
                    tag: el.tagName,
                    id: el.id,
                    className: el.className,
                    text: el.textContent.substring(0, 100)
                }))
        ''')
        print(f"Found {len(result_elements)} elements containing 'черг':")
        for elem in result_elements[:5]:  # Show first 5
            print(f"  {elem}")
        
        # If there's a button, try clicking it
        if buttons:
            print("\nClicking first button...")
            await buttons[0].click()
            await page.wait_for_timeout(2000)
            
            # Check for result again
            result_elements_after = await page.locator('*').evaluate_all('''
                elements => elements
                    .filter(el => el.textContent && el.textContent.toLowerCase().includes('черг'))
                    .map(el => ({
                        tag: el.tagName,
                        id: el.id,
                        className: el.className,
                        text: el.textContent.substring(0, 100)
                    }))
            ''')
            print(f"\nAfter click, found {len(result_elements_after)} elements containing 'черг':")
            for elem in result_elements_after[:5]:
                print(f"  {elem}")
        
        print("\nPress Enter to close browser...")
        input()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_cek_form())
