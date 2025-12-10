import json
import re
import logging
from typing import Dict, Any
from datetime import datetime, timedelta
import pytz
import time

from common.formatting import merge_consecutive_slots

# Botasaurus imports

# Botasaurus imports
from botasaurus.browser import browser, Driver

# --- Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False  # Отключаем дублирование логов

handler = logging.StreamHandler()

def custom_time(*args):
    """Returns current time in Kyiv timezone for logging."""
    return datetime.now(pytz.timezone('Europe/Kiev')).timetuple()

formatter = logging.Formatter(
    '%(asctime)s EET | %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
formatter.converter = custom_time
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)

# --- CEK URLs ---
GROUP_LOOKUP_URL = "https://cek.dp.ua/index.php/cpojivaham/pobutovi-spozhyvachi/viznachennya-chergy.html"
SCHEDULE_URL = "https://cek.dp.ua/index.php/cpojivaham/vidkliuchennia/2-uncategorised/921-grafik-pogodinikh-vidklyuchen.html"

@browser(
    headless=True,
    block_images=False,
    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    reuse_driver=False,
    output=None  # Disable default JSON file output to avoid writing into `out/`
)
def run_parser_service_botasaurus(driver: Driver, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    CEK parser with two-step process using Botasaurus:
    1. Lookup group by address (skip if cached_group provided)
    2. Get schedule by group and date
    """
    city = data.get('city')
    street = data.get('street')
    house = data.get('house')
    cached_group = data.get('cached_group')
    is_debug = data.get('is_debug', False)

    logger.debug(f"CEK Parser (Botasaurus) - Mode: {'Headful (debug)' if is_debug else 'Headless'}")
    logger.debug(f"Address: {city}, {street}, {house}")
    if cached_group:
        logger.debug(f"Using cached group: {cached_group}")
    
    group = cached_group
    
    try:
        # === STEP 1: Get Group by Address (if not cached) ===
        if not group:
            logger.debug("Step 1: Looking up group by address...")
            driver.google_get(GROUP_LOOKUP_URL)
            # Page load is handled by driver
            
            # CEK form has cascading fields
            try:
                # 1. Fill city
                logger.debug("Filling city field...")
                if not driver.select('input#city', wait=10):
                    raise Exception("City input not found")
                
                # Type city
                driver.type('input#city', city)
                # Wait for suggestions to appear
                driver.select('#city-suggestions > div', wait=5)
                
                # Select suggestion
                suggestion = driver.select('#city-suggestions > div:first-child', wait=5)
                if suggestion:
                    suggestion.click()
                    logger.debug("Selected city from suggestions")
                else:
                    logger.debug("No city suggestions appeared")
                time.sleep(0.5)
                
                # 2. Fill street
                logger.debug("Filling street field...")
                # Wait for enabled (Botasaurus select doesn't support :not([disabled]) directly easily, 
                # but we can wait for it or just try typing)
                time.sleep(0.5)  # Brief delay for field to become enabled
                driver.type('input#street', street)
                # Wait for suggestions to appear
                driver.select('#street-suggestions > div', wait=5)
                
                suggestion = driver.select('#street-suggestions > div:first-child', wait=5)
                if suggestion:
                    suggestion.click()
                    logger.debug("Selected street from suggestions")
                else:
                    logger.debug("No street suggestions appeared")
                time.sleep(0.5)
                
                # 3. Fill house
                logger.debug("Filling house field...")
                time.sleep(0.5)  # Brief delay for field to become enabled
                driver.type('input#house', house)
                # Wait for suggestions to appear
                driver.select('#house-suggestions > div', wait=5)
                
                suggestion = driver.select('#house-suggestions > div:first-child', wait=5)
                if suggestion:
                    suggestion.click()
                    logger.debug("Selected house from suggestions")
                else:
                    logger.debug("No house suggestions appeared")
                time.sleep(0.5)
                
                logger.debug("Waiting for group to be calculated...")
                # Wait for group element to appear with result
                driver.select('#group', wait=5)
                
                # Extract group
                group_text = None
                group_elem = driver.select('#group')
                if group_elem:
                    group_text = group_elem.text.strip()
                    logger.debug(f"Found group in #group element: {group_text}")
                
                if not group_text:
                    # Fallback JS search
                    group_text = driver.run_js("""
                        var elements = document.querySelectorAll('*');
                        for (var el of elements) {
                            if (el.textContent && /черга\\s*\\d+\\.\\d+/i.test(el.textContent)) {
                                return el.textContent;
                            }
                        }
                        return null;
                    """)
                    if group_text:
                        logger.debug(f"Found group text via JS: {group_text}")

                if group_text:
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
        logger.debug(f"Step 2: Getting schedule for group {group}...")
        driver.google_get(SCHEDULE_URL)
        # Page load is handled by driver
        
        kiev_tz = pytz.timezone('Europe/Kiev')
        today = datetime.now(kiev_tz)
        tomorrow = today + timedelta(days=1)
        
        schedule = {}
        
        for date_obj in [today, tomorrow]:
            date_str_input = date_obj.strftime('%Y-%m-%d')
            date_str_output = date_obj.strftime('%d.%m.%y')
            
            logger.debug(f"Fetching schedule for {date_str_output} (group {group})...")
            
            try:
                # Select group
                # Botasaurus doesn't have select_option, use JS or click
                driver.run_js(f"""
                    var select = document.querySelector('select#queue');
                    if (select) {{
                        select.value = '{group}';
                        select.dispatchEvent(new Event('change'));
                    }}
                """)
                time.sleep(0.5)
                
                # Fill date
                driver.run_js(f"""
                    var input = document.querySelector('input#date');
                    if (input) {{
                        input.value = '{date_str_input}';
                        input.dispatchEvent(new Event('change'));
                    }}
                """)
                time.sleep(0.5)
                
                # Click submit
                submit_btn = driver.select('input[type="submit"]')
                if submit_btn:
                    submit_btn.click()
                else:
                    # Try JS click
                    driver.run_js("document.querySelector('input[type=\"submit\"]').click()")
                
                time.sleep(2)
                
                # Parse table
                # Simplified parsing logic placeholder
                day_slots = []
                
                # TODO: Implement actual table parsing logic if table structure is known
                # For now returning empty list as in original code
                
                schedule[date_str_output] = day_slots
                
            except Exception as e:
                logger.debug(f"Could not fetch schedule for {date_str_output}: {e}")
                schedule[date_str_output] = []
        
        # Merge slots for cleaner output
        schedule = merge_consecutive_slots(schedule)

        result = {
            "city": city,
            "street": street,
            "house_num": house,
            "group": group,
            "schedule": schedule
        }
        
        # Log result in DEBUG mode
        logger.debug(json.dumps(result, indent=2, ensure_ascii=False))

        return {
            "data": result
        }

    except Exception as e:
        logger.error(f"CEK Parser error: {e}", exc_info=True)
        raise

# Wrapper for compatibility
async def run_parser_service(city: str, street: str, house: str, is_debug: bool = False, skip_input_on_debug: bool = False, cached_group: str = None) -> Dict[str, Any]:
    """Async wrapper for Botasaurus parser."""
    data = {
        'city': city,
        'street': street,
        'house': house,
        'is_debug': is_debug,
        'cached_group': cached_group
    }
    # Note: This calls synchronous code, blocking the event loop.
    # In a production async app, this should ideally run in an executor.
    # For now, we follow the DTEK pattern.
    return run_parser_service_botasaurus(data)

if __name__ == "__main__":
    # Test
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()

    result = run_parser_service_botasaurus({
        'city': "м. Павлоград",
        'street': "вул. Нова",
        'house': "7",
        'is_debug': args.debug
    })
    print(json.dumps(result["data"], indent=2, ensure_ascii=False))
