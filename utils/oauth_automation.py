from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
import time
import re
import os
import pyotp
from urllib.parse import urlparse, parse_qs

class OAuthAutomation:
    def __init__(self, user_id, password, totp_secret):
        self.user_id = user_id
        self.password = password
        self.totp_secret = totp_secret
        self.driver = None
        self.code = None
    
    def setup_driver(self):
        """Setup Chrome driver with options using local chromedriver"""
        chrome_options = Options()
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--start-maximized')
        
        # Get the project root directory (where chromedriver.exe is located)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        chromedriver_path = os.path.join(project_root, 'chromedriver.exe')
        
        if not os.path.exists(chromedriver_path):
            raise FileNotFoundError(f"ChromeDriver not found at {chromedriver_path}")
        
        print(f"Using ChromeDriver at: {chromedriver_path}")
        service = Service(chromedriver_path)
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        print("Chrome browser opened successfully")
        return self.driver
    
    def generate_otp(self):
        """Generate OTP from TOTP secret using PyOTP"""
        if not self.totp_secret:
            return None
        try:
            totp = pyotp.TOTP(self.totp_secret)
            otp = totp.now()
            print(f"Generated OTP: {otp}")
            return otp
        except Exception as e:
            print(f"Error generating OTP: {e}")
            return None
    
    def automate_oauth_login(self, oauth_url):
        """Automate the OAuth login process"""
        try:
            if not self.driver:
                self.setup_driver()
            
            # Navigate to OAuth URL
            print(f"Navigating to OAuth URL...")
            self.driver.get(oauth_url)
            time.sleep(3)  # Wait for page to load
            
            # Wait for login form to appear
            wait = WebDriverWait(self.driver, 30)
            
            print("Looking for TradeStation login form...")
            time.sleep(3)  # Wait for page to fully load
            
            # Try to find and fill Username field using exact XPath
            user_id_field = None
            try:
                # Use exact XPath provided by user
                user_id_field = wait.until(EC.presence_of_element_located((By.XPATH, "//*[@id='username']")))
                
                if user_id_field:
                    # Scroll element into view and wait for it to be clickable
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", user_id_field)
                    time.sleep(1)
                    
                    # Try to click, if intercepted use JavaScript click
                    try:
                        user_id_field.click()
                    except:
                        # If click is intercepted, use JavaScript to click
                        self.driver.execute_script("arguments[0].click();", user_id_field)
                    
                    time.sleep(0.5)
                    user_id_field.clear()
                    time.sleep(0.3)
                    user_id_field.send_keys(self.user_id)
                    print(f"✓ Entered Username: {self.user_id}")
                    time.sleep(1)
                else:
                    raise Exception("Could not find Username field")
            except Exception as e:
                print(f"Error entering Username: {e}")
                raise Exception(f"Failed to find Username field: {e}")
            
            # Try to find and fill Password field using exact XPath
            password_field = None
            try:
                # Use exact XPath provided by user
                password_field = wait.until(EC.presence_of_element_located((By.XPATH, "//*[@id='password']")))
                
                if password_field:
                    # Scroll element into view
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", password_field)
                    time.sleep(1)
                    
                    # Try to click, if intercepted use JavaScript click
                    try:
                        password_field.click()
                    except:
                        # If click is intercepted, use JavaScript to click
                        self.driver.execute_script("arguments[0].click();", password_field)
                    
                    time.sleep(0.5)
                    password_field.clear()
                    time.sleep(0.3)
                    password_field.send_keys(self.password)
                    print("✓ Entered Password")
                    time.sleep(1)
                else:
                    raise Exception("Could not find Password field")
            except Exception as e:
                print(f"Error entering Password: {e}")
                raise Exception(f"Failed to find Password field: {e}")
            
            
            # Try to find and click Log In button using exact XPath
            try:
                # Use exact XPath provided by user
                login_button = wait.until(EC.presence_of_element_located((By.XPATH, "//*[@id='btn-login']")))
                
                if login_button:
                    # Scroll into view
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", login_button)
                    time.sleep(1)
                    
                    # Wait for button to be clickable
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='btn-login']")))
                    
                    # Try to click, if intercepted use JavaScript click
                    try:
                        login_button.click()
                    except:
                        # If click is intercepted, use JavaScript to click
                        self.driver.execute_script("arguments[0].click();", login_button)
                    
                    print("✓ Clicked Log In button")
                    time.sleep(3)
                else:
                    raise Exception("Could not find Log In button")
            except Exception as e:
                print(f"Error clicking Log In button: {e}")
                # Try pressing Enter as fallback
                if password_field:
                    try:
                        password_field.send_keys(Keys.RETURN)
                        print("✓ Pressed Enter to submit (fallback)")
                        time.sleep(3)
                    except:
                        raise Exception(f"Failed to submit login form: {e}")
                else:
                    raise
            
            # Step 2: After clicking Log In, wait for Accept button page (second page)
            print("Waiting for Accept button page...")
            time.sleep(3)  # Wait for page transition
            
            wait_long = WebDriverWait(self.driver, 300)  # 5 minute timeout
            
            # Wait for Accept button to appear
            try:
                # Wait for any button with "Accept" text
                wait_long.until(
                    lambda driver: any(
                        "Accept" in btn.text 
                        for btn in driver.find_elements(By.TAG_NAME, "button")
                    )
                )
                time.sleep(2)  # Give page time to fully render
                
                # Find and click the Accept button
                buttons = self.driver.find_elements(By.TAG_NAME, "button")
                clicked_accept = False
                
                for button in buttons:
                    text = button.text.strip().upper()
                    if "ACCEPT" in text:
                        try:
                            # Scroll into view if needed
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                            time.sleep(0.5)
                            
                            # Try to click, if intercepted use JavaScript click
                            try:
                                button.click()
                            except:
                                self.driver.execute_script("arguments[0].click();", button)
                            
                            print(f"✓ Clicked Accept button: {button.text}")
                            clicked_accept = True
                            time.sleep(3)
                            break
                        except Exception as e:
                            print(f"Error clicking Accept button: {e}")
                            continue
                
                if not clicked_accept:
                    # Try XPath selectors as fallback
                    try:
                        accept_btn = self.driver.find_element(By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept')]")
                        self.driver.execute_script("arguments[0].click();", accept_btn)
                        print("✓ Clicked Accept button via XPath")
                        time.sleep(3)
                    except Exception as e:
                        print(f"Error finding Accept button: {e}")
                        raise
                        
            except Exception as e:
                print(f"Error during Accept button click: {e}")
                # Check if we're already past this page
                current_url = self.driver.current_url
                if 'localhost:3000' in current_url or 'code=' in current_url:
                    print("Already redirected, skipping Accept")
                else:
                    raise Exception(f"Failed to click Accept button: {e}")
            
            # Step 3: Wait for TOTP/OTP verification page (third page)
            print("Waiting for TOTP/OTP verification page...")
            time.sleep(3)  # Wait for page transition
            
            # Generate OTP from TOTP secret
            otp = self.generate_otp()
            
            if otp:
                try:
                    # Wait for the OTP input field to appear using exact XPath
                    print(f"Looking for OTP input field with generated OTP: {otp}")
                    otp_field = wait_long.until(EC.presence_of_element_located((By.XPATH, "//*[@id='code']")))
                    
                    if otp_field:
                        # Scroll into view
                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", otp_field)
                        time.sleep(1)
                        
                        # Wait for field to be clickable
                        wait_long.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='code']")))
                        
                        # Try to click, if intercepted use JavaScript click
                        try:
                            otp_field.click()
                        except:
                            self.driver.execute_script("arguments[0].click();", otp_field)
                        
                        time.sleep(0.5)
                        otp_field.clear()
                        time.sleep(0.3)
                        otp_field.send_keys(otp)
                        print(f"✓ Entered OTP: {otp}")
                        time.sleep(1)
                        
                        # Find and click Continue button using exact XPath
                        try:
                            continue_button = wait_long.until(EC.element_to_be_clickable((By.XPATH, "/html/body/div/main/section/div/div/div/form/div[2]/button")))
                            
                            if continue_button:
                                # Scroll into view
                                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", continue_button)
                                time.sleep(0.5)
                                
                                # Try to click, if intercepted use JavaScript click
                                try:
                                    continue_button.click()
                                except:
                                    self.driver.execute_script("arguments[0].click();", continue_button)
                                
                                print("✓ Clicked Continue button")
                                time.sleep(3)
                            else:
                                raise Exception("Could not find Continue button")
                        except Exception as e:
                            print(f"Error clicking Continue button: {e}")
                            # Try pressing Enter as fallback
                            otp_field.send_keys(Keys.RETURN)
                            print("✓ Pressed Enter after OTP (fallback)")
                            time.sleep(3)
                    else:
                        print("No OTP field found (may not be required)")
                except Exception as e:
                    print(f"Error entering OTP: {e}")
                    # Check if we're already past the OTP page
                    current_url = self.driver.current_url
                    if 'localhost:3000' in current_url or 'code=' in current_url:
                        print("Already redirected, skipping OTP")
                    else:
                        raise Exception(f"Failed to enter OTP: {e}")
            else:
                print("No OTP generated (TOTP secret may be missing)")
            
            # Step 4: Check if we're already redirected or need to click Authorize button
            print("Checking if authorization is complete or Authorize button is needed...")
            time.sleep(3)  # Wait for page transition
            
            # First, check if we're already redirected to localhost:3000 with code
            current_url = self.driver.current_url
            if 'localhost:3000' in current_url and 'code=' in current_url:
                print("✓ Already redirected to localhost:3000 with code, skipping Authorize button")
            else:
                # Check if URL already has code parameter (might be on different domain)
                if 'code=' in current_url:
                    print(f"✓ Found code in URL: {current_url}")
                else:
                    # Need to click Authorize button
                    try:
                        # Wait for any button with "Authorize" text (with shorter timeout)
                        try:
                            WebDriverWait(self.driver, 10).until(
                                lambda driver: any(
                                    "Authorize" in btn.text 
                                    for btn in driver.find_elements(By.TAG_NAME, "button")
                                )
                            )
                            time.sleep(2)  # Give page time to fully render
                            
                            # Find and click the Authorize button
                            buttons = self.driver.find_elements(By.TAG_NAME, "button")
                            clicked_authorize = False
                            
                            for button in buttons:
                                text = button.text.strip().upper()
                                if "AUTHORIZE" in text:
                                    try:
                                        # Scroll into view if needed
                                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                                        time.sleep(0.5)
                                        
                                        # Try to click, if intercepted use JavaScript click
                                        try:
                                            button.click()
                                        except:
                                            self.driver.execute_script("arguments[0].click();", button)
                                        
                                        print(f"✓ Clicked Authorize button: {button.text}")
                                        clicked_authorize = True
                                        time.sleep(3)
                                        break
                                    except Exception as e:
                                        print(f"Error clicking Authorize button: {e}")
                                        continue
                            
                            if not clicked_authorize:
                                # Try XPath selectors as fallback
                                try:
                                    authorize_btn = self.driver.find_element(By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'authorize')]")
                                    self.driver.execute_script("arguments[0].click();", authorize_btn)
                                    print("✓ Clicked Authorize button via XPath")
                                    time.sleep(3)
                                except Exception as e:
                                    print(f"Error finding Authorize button: {e}")
                                    # Check URL again - might have redirected
                                    current_url = self.driver.current_url
                                    if 'code=' in current_url:
                                        print("✓ Found code in URL after timeout")
                                    else:
                                        print(f"Warning: Could not find/click Authorize button")
                                        
                        except Exception as e:
                            print(f"Timeout waiting for Authorize button: {e}")
                            # Check if we're already redirected
                            current_url = self.driver.current_url
                            if 'code=' in current_url:
                                print("✓ Found code in URL despite timeout")
                            else:
                                print(f"Warning: Authorize button not found, but continuing...")
                                
                    except Exception as e:
                        print(f"Error during Authorize button check: {e}")
                        # Check URL anyway
                        current_url = self.driver.current_url
                        if 'code=' in current_url:
                            print("✓ Found code in URL")
                        else:
                            print(f"Warning: Could not process Authorize step, but continuing...")
            
            # Step 5: Wait for redirect to localhost:3000 with code parameter
            print("Waiting for redirect to localhost:3000 with authorization code...")
            
            # Wait for URL to contain localhost:3000 and code parameter
            try:
                wait_long.until(
                    lambda driver: 'localhost:3000' in driver.current_url and 'code=' in driver.current_url
                )
            except Exception as e:
                print(f"Timeout waiting for redirect. Current URL: {self.driver.current_url}")
                # Check current URL anyway
                current_url = self.driver.current_url
                if 'code=' in current_url:
                    print("Found code in URL despite timeout")
                else:
                    raise Exception(f"Failed to redirect to localhost:3000 with code. Current URL: {current_url}")
            
            # Extract code from URL
            current_url = self.driver.current_url
            print(f"✓ Redirected to: {current_url}")
            
            # Parse the code from URL
            parsed_url = urlparse(current_url)
            query_params = parse_qs(parsed_url.query)
            
            if 'code' in query_params:
                self.code = query_params['code'][0]
                print(f"✓ Extracted authorization code: {self.code[:20]}...")
                # Close browser after successful code extraction
                time.sleep(2)  # Brief pause to see the result
                return self.code
            else:
                # Try alternative parsing if query_params didn't work
                if 'code=' in current_url:
                    # Extract code directly from URL string
                    code_match = re.search(r'code=([^&]+)', current_url)
                    if code_match:
                        self.code = code_match.group(1)
                        print(f"✓ Extracted authorization code (alternative method): {self.code[:20]}...")
                        time.sleep(2)
                        return self.code
                
                raise Exception(f"No 'code' parameter found in redirect URL: {current_url}")
                
        except Exception as e:
            print(f"Error during OAuth automation: {e}")
            raise
        finally:
            # Browser will be closed by the close() method called from app.py
            pass
    
    def close(self):
        """Close the browser"""
        if self.driver:
            self.driver.quit()
            self.driver = None
