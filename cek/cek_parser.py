import asyncio
import json
import re
import os
from playwright.async_api import async_playwright, TimeoutError
from pathlib import Path
import logging
from typing import Dict, Any
from datetime import datetime, timedelta
import pytz

# --- Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    '%(asctime)s %(name)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

# --- Configuration ---
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")

# --- CEK URLs ---
GROUP_LOOKUP_URL = "https://cek.dp.ua/index.php/cpojivaham/pobutovi-spozhyvachi/viznachennya-chergy.html"
SCHEDULE_URL = "https://cek.dp.ua/index.php/cpojivaham/vidkliuchennia/2-uncategorised/921-grafik-pogodinikh-vidklyuchen.html"


async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False, skip_input_on_debug: bool = False, cached_group: str = None) -> Dict[str, Any]:
    """
    CEK parser with two-step process:
    1. Lookup group by address (skip if cached_group provided)
    2. Get schedule by group and date
    
    Args:
        city: City name (e.g., "Дніпро")
        street: Street name (e.g., "вул. Казака Мамая")
        house: House number (e.g., "10")
        is_debug: Run in headful mode for debugging
        skip_input_on_debug: Skip input() prompts in debug mode
        cached_group: Pre-determined group to skip step 1 (e.g., "1.1")
    """
    
    run_headless = not is_debug
    logger.info(f"CEK Parser - Mode: {'Headless' if run_headless else 'Headful (debug)'}")
    logger.info(f"Address: {city}, {street}, {house}")
    if cached_group:
        logger.info(f"Using cached group: {cached_group}")
    
    out_path = Path(OUT_DIR)
    out_path.mkdir(exist_ok=True)
    
    group = cached_group
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=run_headless)
        page = await browser.new_page()
        
        try:
            # === STEP 1: Get Group by Address (if not cached) ===
            if not group:
                logger.info("Step 1: Looking up group by address...")
                await page.goto(GROUP_LOOKUP_URL, wait_until="load", timeout=60000)
                await page.wait_for_timeout(2000)
                
                # CEK form has cascading fields - each field becomes enabled after previous is filled
                try:
                    # 1. Fill city (should be enabled by default)
                    logger.info("Filling city field...")
                    await page.wait_for_selector('input#city:not([disabled])', timeout=10000)
                    
                    # Type the city to trigger autocomplete
                    await page.type('input#city', city, delay=30)
                    await page.wait_for_timeout(500)
                    
                    # Try to interact with city suggestions
                    try:
                        # Wait for suggestions dropdown
                        await page.wait_for_selector('#city-suggestions > div', state='visible', timeout=2000)
                        # Click first suggestion
                        await page.click('#city-suggestions > div:first-child')
                        logger.info("Selected city from suggestions")
                        await page.wait_for_timeout(300)
                    except:
                        logger.warning("No city suggestions appeared")
                        await page.wait_for_timeout(300)
                    
                    # 2. Fill street (should now be enabled)
                    logger.info("Filling street field...")
                    # Wait for street field to become enabled
                    await page.wait_for_selector('input#street:not([disabled])', timeout=5000)
                    
                    await page.type('input#street', street, delay=30)
                    await page.wait_for_timeout(500)
                    
                    # Wait for street suggestions
                    try:
                        await page.wait_for_selector('#street-suggestions > div', state='visible', timeout=2000)
                        await page.click('#street-suggestions > div:first-child')
                        logger.info("Selected street from suggestions")
                        await page.wait_for_timeout(300)
                    except:
                        logger.warning("No street suggestions appeared")
                        await page.wait_for_timeout(300)
                    
                    # 3. Fill house (should now be enabled)
                    logger.info("Filling house field...")
                    await page.wait_for_selector('input#house:not([disabled])', timeout=5000)
                    
                    await page.type('input#house', house, delay=30)
                    await page.wait_for_timeout(500)
                    
                    # Wait for house suggestions
                    try:
                        await page.wait_for_selector('#house-suggestions > div', state='visible', timeout=2000)
                        await page.click('#house-suggestions > div:first-child')
                        logger.info("Selected house from suggestions")
                        await page.wait_for_timeout(500)
                    except:
                        logger.warning("No house suggestions appeared")
                        await page.wait_for_timeout(500)
                    
                    # Form auto-updates after house selection, wait for group to appear
                    logger.info("Waiting for group to be calculated...")
                    await page.wait_for_timeout(1000)
                    
                    # DEBUG: Take screenshot to see what's on the page
                    if is_debug:
                        screenshot_path = out_path / f"cek_after_form_{city}_{street}_{house}.png"
                        await page.screenshot(path=str(screenshot_path))
                        logger.info(f"Debug screenshot saved to {screenshot_path}")
                    
                    # Extract group from result
                    # The result is displayed in element with id="group"
                    group_text = None
                    try:
                        # Try to read from #group element
                        group_element = page.locator('#group')
                        group_text = await group_element.inner_text(timeout=5000)
                        group_text = group_text.strip()
                        logger.info(f"Found group in #group element: {group_text}")
                    except Exception as e:
                        logger.warning(f"Could not read #group element: {e}")
                        # Fallback: try to find text like "Черга 1.1" or similar
                        try:
                            group_element = page.locator('text=/Черга\\s*\\d+\\.\\d+/i').first
                            group_text = await group_element.inner_text(timeout=5000)
                            logger.info(f"Found group text via locator: {group_text}")
                        except:
                            # Alternative: look for any element containing the pattern
                            try:
                                group_text = await page.evaluate('''() => {
                                    const elements = Array.from(document.querySelectorAll('*'));
                                    for (const el of elements) {
                                        const text = el.textContent;
                                        if (text && /черга\\s*\\d+\\.\\d+/i.test(text)) {
                                            return text;
                                        }
                                    }
                                    return null;
                                }''')
                                if group_text:
                                    logger.info(f"Found group text via JavaScript: {group_text}")
                            except Exception as js_error:
                                logger.warning(f"JavaScript search failed: {js_error}")
                                pass
                    
                    if group_text:
                        # Extract just the number part (e.g., "1.1" or "3.2")
                        # The #group element should contain just the number
                        match = re.search(r'(\d+\.\d+)', group_text)
                        if match:
                            group = match.group(1)
                            logger.info(f"Successfully extracted group: {group}")
                        else:
                            raise ValueError(f"Could not extract group number from: {group_text}")
                    else:
                        raise ValueError("Could not find group information on page")
                        
                except Exception as e:
                    logger.error(f"Error in Step 1 (group lookup): {e}")
                    raise ValueError(f"Could not determine group for address {city}, {street}, {house}. Error: {e}")
            
            # === STEP 2: Get Schedule by Group ===
            logger.info(f"Step 2: Getting schedule for group {group}...")
            await page.goto(SCHEDULE_URL, wait_until="load", timeout=60000)
            await page.wait_for_timeout(1000)
            
            # Get today and tomorrow dates
            kiev_tz = pytz.timezone('Europe/Kiev')
            today = datetime.now(kiev_tz)
            tomorrow = today + timedelta(days=1)
            
            schedule = {}
            
            # Get schedule for today and tomorrow
            for day_offset, date_obj in enumerate([today, tomorrow]):
                date_str_input = date_obj.strftime('%Y-%m-%d')  # Format for HTML5 date input: YYYY-MM-DD
                date_str_output = date_obj.strftime('%d.%m.%y')  # Format for output: DD.MM.YY
                
                logger.info(f"Fetching schedule for {date_str_output} (group {group})...")
                
                try:
                    # Select group from dropdown
                    await page.select_option('select#queue', value=group)
                    await page.wait_for_timeout(200)
                    
                    # Fill date (HTML5 date input requires YYYY-MM-DD format)
                    await page.fill('input#date', date_str_input)
                    await page.wait_for_timeout(200)
                    
                    # Click submit button
                    await page.click('input[type="submit"]')
                    await page.wait_for_timeout(1000)
                    
                    # Parse the schedule table
                    # Look for a table with shutdown times
                    # This is a simplified version - actual implementation depends on table structure
                    day_slots = []
                    
                    # Try to find schedule table rows
                    rows = await page.locator('table tr').all()
                    for row in rows:
                        cells = await row.locator('td').all()
                        if len(cells) >= 2:
                            # Assuming format: Time | Status or similar
                            # Extract time ranges where there are shutdowns
                            # This is placeholder logic - needs actual table structure
                            pass
                    
                    # For now, return empty schedule as placeholder
                    schedule[date_str_output] = day_slots
                    
                except Exception as e:
                    logger.warning(f"Could not fetch schedule for {date_str_output}: {e}")
                    schedule[date_str_output] = []
            
            result = {
                "city": city,
                "street": street,
                "house_num": house,
                "group": group,
                "schedule": schedule
            }
            
            if is_debug and not skip_input_on_debug:
                input("Press Enter to close browser...")
            
            return {
                "data": result,
                "json_path": None,
                "png_path": None
            }
            
        except Exception as e:
            logger.error(f"CEK Parser error: {e}")
            if is_debug and not skip_input_on_debug:
                input("Error occurred. Press Enter to close browser...")
            raise e
        finally:
            if not is_debug:
                await browser.close()



if __name__ == "__main__":
    # Test
    async def test():
        result = await run_parser_service(
            city="Дніпро",
            street="вул. Казака Мамая",
            house="10",
            is_debug=True
        )
        print(json.dumps(result["data"], indent=2, ensure_ascii=False))
    
    asyncio.run(test())
