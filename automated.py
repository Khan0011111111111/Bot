"""
UK Driving Test Booking Automation Script

LEGAL DISCLAIMER:
This script is provided for educational purposes only. Users must:
1. Comply with the terms of service of any website they interact with
2. Ensure they have permission to automate interactions with the target website
3. Use this script responsibly and ethically
4. Be aware that automated booking systems may be against the terms of service
5. Take full responsibility for any consequences of using this script

The author does not endorse or encourage any use that violates terms of service,
disrupts services, or causes harm to any systems.
"""

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import aiohttp
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Request, Response
import twilio.rest
from twocaptcha import TwoCaptcha

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("booking_automation.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("booking_automation")

# Constants
BASE_URL = "https://www.gov.uk/book-pupil-driving-test"
LOGIN_URL = "https://driverpracticaltest.dvsa.gov.uk/login"
BOOKING_URL = "https://driverpracticaltest.dvsa.gov.uk/manage"


class TestType(Enum):
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    LGV = "lgv"
    PCV = "pcv"


@dataclass
class TestCenter:
    id: str
    name: str
    distance: Optional[float] = None


@dataclass
class DateRange:
    start_date: datetime
    end_date: datetime


@dataclass
class TimePreference:
    earliest: str  # Format: "HH:MM" in 24-hour format
    latest: str    # Format: "HH:MM" in 24-hour format


@dataclass
class Credentials:
    license_number: str
    application_reference: str


@dataclass
class ProxyConfig:
    host: str
    port: int
    username: str
    password: str
    rotation_interval: int  # in seconds


@dataclass
class NotificationConfig:
    # Email settings
    email_enabled: bool = False
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    sender_email: str = ""
    recipient_email: str = ""
    
    # SMS settings (Twilio)
    sms_enabled: bool = False
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_to_number: str = ""


@dataclass
class Config:
    credentials: Credentials
    test_type: TestType
    test_centers: List[TestCenter]
    date_range: DateRange
    time_preference: TimePreference
    preferred_days: Set[int]  # 0=Monday, 6=Sunday
    proxies: List[ProxyConfig]
    twocaptcha_api_key: str
    notification: NotificationConfig
    check_interval: Tuple[int, int]  # min, max seconds between checks
    headless: bool = False


class FingerPrintGenerator:
    """Generate randomized but realistic browser fingerprints"""
    
    CHROME_VERSIONS = [
        "112.0.5615.49", "112.0.5615.121", "112.0.5615.138",
        "113.0.5672.63", "113.0.5672.92", "113.0.5672.127",
        "114.0.5735.90", "114.0.5735.106", "114.0.5735.133",
        "115.0.5790.102", "115.0.5790.171"
    ]
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
    ]
    
    VIEWPORT_SIZES = [
        {"width": 1366, "height": 768},
        {"width": 1440, "height": 900},
        {"width": 1536, "height": 864},
        {"width": 1600, "height": 900},
        {"width": 1920, "height": 1080},
        {"width": 2048, "height": 1152},
        {"width": 2560, "height": 1440}
    ]
    
    @classmethod
    def generate(cls) -> Dict:
        """Generate a random fingerprint configuration"""
        chrome_version = random.choice(cls.CHROME_VERSIONS)
        user_agent_template = random.choice(cls.USER_AGENTS)
        user_agent = user_agent_template.format(version=chrome_version)
        viewport = random.choice(cls.VIEWPORT_SIZES)
        
        return {
            "userAgent": user_agent,
            "viewport": viewport,
            "deviceScaleFactor": random.choice([1, 1.25, 1.5, 2]),
            "hasTouch": random.choice([True, False]),
            "locale": random.choice(["en-GB", "en-US"]),
            "timezoneId": "Europe/London",
        }


class MousePatternGenerator:
    """Generate human-like mouse movements"""
    
    @staticmethod
    async def move_like_human(page: Page, target_x: int, target_y: int):
        """Simulate human-like mouse movement to a target position"""
        # Get current viewport size
        viewport = await page.viewport_size()
        if not viewport:
            viewport = {"width": 1366, "height": 768}
        
        # Start from a random position
        current_x = random.randint(0, viewport["width"])
        current_y = random.randint(0, viewport["height"])
        
        # Calculate distance to target
        distance = ((target_x - current_x) ** 2 + (target_y - current_y) ** 2) ** 0.5
        
        # Determine number of steps based on distance
        steps = max(10, int(distance / 10))
        
        # Add some randomness to the path with Bezier curve simulation
        control_x = current_x + (target_x - current_x) / 2 + random.randint(-100, 100)
        control_y = current_y + (target_y - current_y) / 2 + random.randint(-100, 100)
        
        await page.mouse.move(current_x, current_y)
        
        for i in range(1, steps + 1):
            # Bezier curve calculation for a single control point
            t = i / steps
            one_minus_t = 1 - t
            
            x = one_minus_t**2 * current_x + 2 * one_minus_t * t * control_x + t**2 * target_x
            y = one_minus_t**2 * current_y + 2 * one_minus_t * t * control_y + t**2 * target_y
            
            # Add small random fluctuations to simulate hand tremor
            x += random.randint(-3, 3)
            y += random.randint(-3, 3)
            
            # Ensure we stay within viewport
            x = max(0, min(viewport["width"], x))
            y = max(0, min(viewport["height"], y))
            
            # Random delay between movements
            delay = random.uniform(5, 15)
            await page.mouse.move(x, y, steps={"delay": delay})
            
            # Random pause during movement
            if random.random() < 0.2:
                await asyncio.sleep(random.uniform(0.1, 0.3))
    
    @staticmethod
    async def realistic_click(page: Page, selector: str):
        """Perform a realistic mouse movement and click on an element"""
        # Wait for element to be visible
        element = await page.wait_for_selector(selector, state="visible")
        if not element:
            logger.error(f"Element with selector '{selector}' not found")
            return False
        
        # Get element's bounding box
        box = await element.bounding_box()
        if not box:
            logger.error(f"Couldn't get bounding box for selector '{selector}'")
            return False
        
        # Calculate a random point within the element
        target_x = box["x"] + random.uniform(5, box["width"] - 5)
        target_y = box["y"] + random.uniform(5, box["height"] - 5)
        
        # Move mouse like a human
        await MousePatternGenerator.move_like_human(page, target_x, target_y)
        
        # Random delay before clicking (hesitation)
        await asyncio.sleep(random.uniform(0.1, 0.5))
        
        # Click with random duration
        await page.mouse.click(target_x, target_y, delay=random.randint(50, 150))
        
        # Random delay after clicking
        await asyncio.sleep(random.uniform(0.2, 0.7))
        
        return True


class HoneypotDetector:
    """Detect and handle honeypot fields that may be used to detect bots"""
    
    @staticmethod
    async def identify_and_avoid_honeypots(page: Page):
        """Identify potential honeypot fields and ensure we don't interact with them"""
        # Common honeypot patterns
        honeypot_selectors = [
            # Hidden fields with names suggesting user interaction
            'input[type="text"][style*="display: none"]',
            'input[type="text"][style*="visibility: hidden"]',
            'input[style*="opacity: 0"]',
            # Fields with suspicious names
            'input[name*="hp"]',
            'input[name*="honey"]',
            'input[name*="pot"]',
            'input[name*="bot"]',
            # Fields positioned off-screen
            'input[style*="position: absolute"][style*="left: -"]',
            'input[style*="position: absolute"][style*="top: -"]',
        ]

        # Check for potential honeypots
        for selector in honeypot_selectors:
            honeypot_elements = await page.query_selector_all(selector)
            for element in honeypot_elements:
                # Log the detected honeypot
                name_attr = await element.get_attribute("name")
                id_attr = await element.get_attribute("id")
                logger.info(f"Potential honeypot detected: {name_attr or id_attr or 'unnamed'}")
                
                # For some honeypots, we may need to set a value
                # This is tricky as we need to decide whether it should be left empty or filled
                visible = await element.is_visible()
                if not visible:
                    # If it's hidden, it's likely a honeypot that should be left empty
                    logger.info(f"Leaving honeypot field empty: {name_attr or id_attr or 'unnamed'}")
                    continue


class CaptchaSolver:
    """Handle CAPTCHA solving using 2Captcha API"""
    
    def __init__(self, api_key: str):
        self.solver = TwoCaptcha(api_key)
    
    async def solve_recaptcha(self, page: Page, site_key: str, site_url: str) -> str:
        """
        Solve a reCAPTCHA challenge
        Returns the solution token
        """
        logger.info("Attempting to solve reCAPTCHA...")
        
        try:
            # We need to run the 2Captcha API call in a separate thread
            # since it's a blocking operation
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.solver.recaptcha(
                    sitekey=site_key,
                    url=site_url
                )
            )
            
            token = result.get('code')
            logger.info("Successfully solved reCAPTCHA")
            return token
            
        except Exception as e:
            logger.error(f"Failed to solve CAPTCHA: {str(e)}")
            raise
    
    async def inject_recaptcha_solution(self, page: Page, token: str):
        """Inject reCAPTCHA solution token into the page"""
        logger.info("Injecting reCAPTCHA solution...")
        
        # JavaScript to set the reCAPTCHA response
        js_code = f"""
        document.querySelector('[name="g-recaptcha-response"]').innerHTML = "{token}";
        document.querySelector('[name="g-recaptcha-response"]').value = "{token}";
        
        // Also set in the grecaptcha object if it exists
        if (typeof grecaptcha !== 'undefined') {{
            grecaptcha.enterprise?.getResponse = function() {{ return "{token}"; }};
            grecaptcha.getResponse = function() {{ return "{token}"; }};
        }}
        """
        
        await page.evaluate(js_code)
        logger.info("reCAPTCHA solution injected")


class CloudflareBypass:
    """Techniques to bypass Cloudflare protection"""
    
    @staticmethod
    async def setup_browser_for_cf_bypass(context: BrowserContext):
        """Configure browser to better handle Cloudflare challenges"""
        # Set specific headers that help avoid triggering Cloudflare
        await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false
        });
        
        // Hide automation-related properties
        if (window.navigator.plugins) {
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
                    { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
                    { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer' },
                    { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer' }
                ]
            });
        }
        
        // Add language and platform details
        Object.defineProperty(navigator, 'language', {
            get: () => 'en-GB'
        });
        
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
        """)
    
    @staticmethod
    async def handle_cf_challenge(page: Page, timeout: int = 30):
        """
        Wait for and handle Cloudflare challenge if encountered
        Returns True if challenge was handled, False if no challenge detected
        """
        try:
            # Check if we hit a Cloudflare challenge
            cf_detected = await page.wait_for_selector(
                "div#cf-challenge-running, iframe[src*='challenges.cloudflare.com']",
                timeout=5000
            )
            
            if cf_detected:
                logger.info("Cloudflare challenge detected. Waiting for it to resolve...")
                
                # Wait for CF to load challenge
                await asyncio.sleep(2)
                
                # Check if it's a JS challenge that resolves automatically
                # If automatic resolution doesn't happen, we may need to add
                # interaction for more complex challenges
                
                # Wait for the page to load after challenge
                try:
                    # Wait for CF challenge to disappear or main content to appear
                    await page.wait_for_function("""
                        () => !document.querySelector("div#cf-challenge-running") ||
                              document.body.textContent.includes("Book your driving test")
                    """, timeout=timeout * 1000)
                    
                    logger.info("Cloudflare challenge passed")
                    return True
                    
                except Exception as e:
                    logger.error(f"Failed to pass Cloudflare challenge: {str(e)}")
                    # Take a screenshot for debugging
                    await page.screenshot(path=f"cloudflare_challenge_failed_{int(time.time())}.png")
                    raise Exception("Failed to pass Cloudflare challenge")
            
            return False
            
        except Exception as e:
            if "Timeout" in str(e):
                # No Cloudflare challenge detected
                return False
            else:
                logger.error(f"Error handling Cloudflare challenge: {str(e)}")
                raise


class ProxyRotator:
    """Manage and rotate through a pool of proxies"""
    
    def __init__(self, proxies: List[ProxyConfig]):
        self.proxies = proxies
        self.current_index = 0
        self.last_rotation_time = time.time()
    
    def get_current_proxy(self) -> ProxyConfig:
        """Get the current proxy configuration"""
        return self.proxies[self.current_index]
    
    def rotate_if_needed(self) -> bool:
        """
        Rotate to the next proxy if the rotation interval has passed
        Returns True if rotation occurred
        """
        current_proxy = self.get_current_proxy()
        elapsed_time = time.time() - self.last_rotation_time
        
        if elapsed_time >= current_proxy.rotation_interval:
            self.rotate()
            return True
        return False
    
    def rotate(self) -> ProxyConfig:
        """Rotate to the next proxy and return it"""
        self.current_index = (self.current_index + 1) % len(self.proxies)
        self.last_rotation_time = time.time()
        logger.info(f"Rotated to proxy: {self.get_current_proxy().host}:{self.get_current_proxy().port}")
        return self.get_current_proxy()
    
    def get_proxy_url(self) -> str:
        """Get the current proxy URL in the format required by Playwright"""
        proxy = self.get_current_proxy()
        return f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"


class Notifier:
    """Handle notifications via email and SMS"""
    
    def __init__(self, config: NotificationConfig):
        self.config = config
    
    async def send_email_notification(self, subject: str, message: str) -> bool:
        """Send an email notification"""
        if not self.config.email_enabled:
            logger.info("Email notifications are disabled")
            return False
        
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config.sender_email
            msg['To'] = self.config.recipient_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(message, 'plain'))
            
            with smtplib.SMTP(self.config.smtp_server, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_username, self.config.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email notification sent to {self.config.recipient_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email notification: {str(e)}")
            return False
    
    async def send_sms_notification(self, message: str) -> bool:
        """Send an SMS notification via Twilio"""
        if not self.config.sms_enabled:
            logger.info("SMS notifications are disabled")
            return False
        
        try:
            # Initialize Twilio client
            client = twilio.rest.Client(self.config.twilio_account_sid, self.config.twilio_auth_token)
            
            # Send message
            message = client.messages.create(
                body=message,
                from_=self.config.twilio_from_number,
                to=self.config.twilio_to_number
            )
            
            logger.info(f"SMS notification sent to {self.config.twilio_to_number}, SID: {message.sid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send SMS notification: {str(e)}")
            return False
    
    async def notify_success(self, test_center: str, test_date: str, test_time: str) -> None:
        """Send successful booking notifications through configured channels"""
        message = f"Success! Driving test booked at {test_center} on {test_date} at {test_time}."
        
        if self.config.email_enabled:
            await self.send_email_notification(
                subject="Driving Test Booking Successful!",
                message=message
            )
        
        if self.config.sms_enabled:
            await self.send_sms_notification(message)


class DrivingTestBooker:
    """Main class to handle driving test booking automation"""
    
    def __init__(self, config_path: str):
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        
        # Parse credentials
        credentials = Credentials(
            license_number=config_data["credentials"]["license_number"],
            application_reference=config_data["credentials"]["application_reference"]
        )
        
        # Parse test type
        test_type = TestType(config_data["test_type"])
        
        # Parse test centers
        test_centers = [
            TestCenter(
                id=center["id"],
                name=center["name"],
                distance=center.get("distance")
            )
            for center in config_data["test_centers"]
        ]
        
        # Parse date range
        date_range = DateRange(
            start_date=datetime.strptime(config_data["date_range"]["start_date"], "%Y-%m-%d"),
            end_date=datetime.strptime(config_data["date_range"]["end_date"], "%Y-%m-%d")
        )
        
        # Parse time preference
        time_preference = TimePreference(
            earliest=config_data["time_preference"]["earliest"],
            latest=config_data["time_preference"]["latest"]
        )
        
        # Parse preferred days
        preferred_days = set(config_data["preferred_days"])
        
        # Parse proxies
        proxies = [
            ProxyConfig(
                host=proxy["host"],
                port=proxy["port"],
                username=proxy["username"],
                password=proxy["password"],
                rotation_interval=proxy["rotation_interval"]
            )
            for proxy in config_data["proxies"]
        ]
        
        # Parse notification config
        notification = NotificationConfig(
            email_enabled=config_data["notification"].get("email_enabled", False),
            smtp_server=config_data["notification"].get("smtp_server", ""),
            smtp_port=config_data["notification"].get("smtp_port", 587),
            smtp_username=config_data["notification"].get("smtp_username", ""),
            smtp_password=config_data["notification"].get("smtp_password", ""),
            sender_email=config_data["notification"].get("sender_email", ""),
            recipient_email=config_data["notification"].get("recipient_email", ""),
            sms_enabled=config_data["notification"].get("sms_enabled", False),
            twilio_account_sid=config_data["notification"].get("twilio_account_sid", ""),
            twilio_auth_token=config_data["notification"].get("twilio_auth_token", ""),
            twilio_from_number=config_data["notification"].get("twilio_from_number", ""),
            twilio_to_number=config_data["notification"].get("twilio_to_number", "")
        )
        
        # Create the final config
        self.config = Config(
            credentials=credentials,
            test_type=test_type,
            test_centers=test_centers,
            date_range=date_range,
            time_preference=time_preference,
            preferred_days=preferred_days,
            proxies=proxies,
            twocaptcha_api_key=config_data["twocaptcha_api_key"],
            notification=notification,
            check_interval=(
                config_data["check_interval"]["min"],
                config_data["check_interval"]["max"]
            ),
            headless=config_data.get("headless", False)
        )
        
        # Initialize components
        self.proxy_rotator = ProxyRotator(self.config.proxies)
        self.captcha_solver = CaptchaSolver(self.config.twocaptcha_api_key)
        self.notifier = Notifier(self.config.notification)
        
        # State tracking
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.logged_in = False
    
    async def setup_browser(self):
        """Initialize and configure the browser for automation"""
        logger.info("Setting up browser...")
        
        playwright = await async_playwright().start()
        
        # Generate fingerprint for browser
        fingerprint = FingerPrintGenerator.generate()
        
        # Configure proxy
        proxy_url = self.proxy_rotator.get_proxy_url()
        
        # Launch browser with custom settings
        self.browser = await playwright.chromium.launch(
            headless=self.config.headless,
            proxy={
                "server": proxy_url,
            },
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-site-isolation-trials",
            ]
        )
        
        # Create a context with custom settings
        self.context = await self.browser.new_context(
            user_agent=fingerprint["userAgent"],
            viewport=fingerprint["viewport"],
            device_scale_factor=fingerprint["deviceScaleFactor"],
            has_touch=fingerprint["hasTouch"],
            locale=fingerprint["locale"],
            timezone_id=fingerprint["timezoneId"],
            permissions=["geolocation"],
            geolocation={"latitude": 51.5074, "longitude": 0.1278},  # London coordinates
        )
        
        # Set up event listeners for requests/responses for debugging
        self.context.on("request", self.handle_request)
        self.context.on("response", self.handle_response)
        
        # Set up Cloudflare bypass
        await CloudflareBypass.setup_browser_for_cf_bypass(self.context)
        
        # Create a new page
        self.page = await self.context.new_page()
        
        # Add additional scripts to evade detection
        await self.page.add_init_script("""
        // Override navigator properties to evade fingerprinting
        const originalGetParameter = URLSearchParams.prototype.get;
        URLSearchParams.prototype.get = function(name) {
            // Detect potential fingerprinting attempts
            if (name === 'automation' || name === 'webdriver' || name === 'driver' || name === 'selenium') {
                return null;
            }
            return originalGetParameter.call(this, name);
        };
        
        // Add canvas noise to prevent fingerprinting
        const originalGetContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function() {
            const context = originalGetContext.apply(this, arguments);
            if (context && arguments[0] === '2d') {
                const originalFillText = context.fillText;
                context.fillText = function() {
                    originalFillText.apply(this, arguments);
                    // Add slight noise to canvas data
                    const imageData = context.getImageData(0, 0, this.canvas.width, this.canvas.height);
                    const pixels = imageData.data;
                    // Modify a few random pixels slightly
                    for (let i = 0; i < 20; i++) {
                        const idx = Math.floor(Math.random() * pixels.length / 4) * 4;
                        pixels[idx] = Math.max(0, Math.min(255, pixels[idx] + Math.floor(Math.random() * 3) - 1));
                    }
                    context.putImageData(imageData, 0, 0);
                };
            }
            return context;
        };
        """)
        
        logger.info("Browser setup complete")
        return self.page
    
    async def handle_request(self, request: Request):
        """Handle and monitor outgoing requests"""
        if "api" in request.url or "ajax" in request.url:
            logger.debug(f"REQUEST: {request.method} {request.url}")
    
    async def handle_response(self, response: Response):
        """Handle and monitor incoming responses"""
        if "api" in response.url or "ajax" in response.url:
            logger.debug(f"RESPONSE: {response.status} {response.url}")
            
            if response.status == 403 or response.status == 429:
                logger.warning(f"Potential blocking detected: {response.status} response from {response.url}")
                
                # Check if we should rotate the proxy
                if self.proxy_rotator.rotate_if_needed():
                    logger.info("Rotating proxy due to potential blocking")
                    await self.restart_session()
    
    async def restart_session(self):
        """Restart the browser session with a new proxy"""
        logger.info("Restarting browser session...")
        
        # Close current session
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        
        # Reset state
        self.logged_in = False
        
        # Set up browser again
        await self.setup_browser()
        
        logger.info("Browser session restarted with new proxy")
    
    async def random_delay(self, min_seconds: float = None, max_seconds: float = None):
        """Wait for a random period of time to simulate human behavior"""
        if min_seconds is None:
            min_seconds = self.config.check_interval[0]
        if max_seconds is None:
            max_seconds = self.config.check_interval[1]
            
        delay = random.uniform(min_seconds, max_seconds)
        logger.debug(f"Random delay: {delay:.2f} seconds")
        await asyncio.sleep(delay)
    
    async def login(self) -> bool:
        """Login to the driving test booking system"""
        if self.logged_in:
            logger.info("Already logged in")
            return True
            
        logger.info("Starting login process...")
        
        try:
            # Go to the landing page first
            await self.page.goto(BASE_URL, wait_until="networkidle")
            
            # Handle Cloudflare if present
            await CloudflareBypass.handle_cf_challenge(self.page)
            
            # Click on the link to start booking
            #await MousePatternGenerator.realistic_click(self.page, "a:
            # Click on the link to start booking
            await MousePatternGenerator.realistic_click(self.page, "a:text('Start now')")
            
            # Wait for page load with random delay
            await self.random_delay(2, 5)
            
            # Navigate to the login page
            await self.page.goto(LOGIN_URL, wait_until="networkidle")
            await CloudflareBypass.handle_cf_challenge(self.page)
            
            # Check for honeypot fields
            await HoneypotDetector.identify_and_avoid_honeypots(self.page)
            
            # Fill in credentials with human-like typing
            await self.type_like_human("#driving-licence-number", self.config.credentials.license_number)
            await self.random_delay(0.5, 1.5)
            await self.type_like_human("#application-reference-number", self.config.credentials.application_reference)
            
            # Check for CAPTCHA
            recaptcha_element = await self.page.query_selector('[data-sitekey]')
            if recaptcha_element:
                logger.info("reCAPTCHA detected, attempting to solve...")
                site_key = await recaptcha_element.get_attribute('data-sitekey')
                token = await self.captcha_solver.solve_recaptcha(self.page, site_key, LOGIN_URL)
                await self.captcha_solver.inject_recaptcha_solution(self.page, token)
            
            # Submit the form
            await self.random_delay(1, 2)
            await MousePatternGenerator.realistic_click(self.page, "#booking-login")
            
            # Wait for login result
            try:
                await self.page.wait_for_selector("#find-test-centres, .error-summary", timeout=10000)
                
                # Check if login failed
                error = await self.page.query_selector(".error-summary")
                if error:
                    error_text = await error.text_content()
                    logger.error(f"Login failed: {error_text}")
                    return False
                
                logger.info("Login successful")
                self.logged_in = True
                return True
                
            except Exception as e:
                logger.error(f"Error waiting for login result: {str(e)}")
                return False
            
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            # Take screenshot for debugging
            await self.page.screenshot(path=f"login_error_{int(time.time())}.png")
            return False
    
    async def type_like_human(self, selector: str, text: str):
        """Type text in a human-like manner with variable speed and occasional mistakes"""
        # Wait for the element
        element = await self.page.wait_for_selector(selector)
        if not element:
            logger.error(f"Element with selector '{selector}' not found")
            return False
        
        # Clear any existing content
        await element.click(click_count=3)  # Triple click to select all
        await self.page.keyboard.press("Backspace")
        
        # Type with variable speed
        for char in text:
            # Randomly make a mistake and correct it (3% chance)
            if random.random() < 0.03:
                wrong_char = random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                await element.type(wrong_char, delay=random.randint(50, 200))
                await asyncio.sleep(random.uniform(0.1, 0.3))
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(random.uniform(0.1, 0.2))
            
            # Type the correct character
            await element.type(char, delay=random.randint(50, 200))
            
            # Occasionally pause as if thinking
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.1, 0.5))
        
        return True
    
    async def navigate_to_test_search(self):
        """Navigate to the test center search page"""
        logger.info("Navigating to test search page...")
        
        # Make sure we're logged in
        if not self.logged_in:
            success = await self.login()
            if not success:
                logger.error("Failed to navigate to test search - login failed")
                return False
        
        try:
            # Check if we're already on the manage booking page
            if not await self.page.query_selector("#find-test-centres"):
                # Navigate to the manage booking page
                await self.page.goto(BOOKING_URL, wait_until="networkidle")
                await CloudflareBypass.handle_cf_challenge(self.page)
            
            # Check if we need to select test type
            test_type_button = await self.page.query_selector(f"a:text-matches('{self.config.test_type.value}', 'i')")
            if test_type_button:
                await MousePatternGenerator.realistic_click(self.page, f"a:text-matches('{self.config.test_type.value}', 'i')")
                await self.random_delay(1, 3)
            
            # Wait for test center search to be available
            await self.page.wait_for_selector("#test-centres-near-you")
            logger.info("Successfully navigated to test search page")
            return True
            
        except Exception as e:
            logger.error(f"Failed to navigate to test search page: {str(e)}")
            await self.page.screenshot(path=f"navigate_error_{int(time.time())}.png")
            return False
    
    async def search_test_center(self, test_center: TestCenter) -> bool:
        """Search for a specific test center"""
        logger.info(f"Searching for test center: {test_center.name}")
        
        try:
            # Navigate to test search if needed
            if not await self.page.query_selector("#test-centres-near-you"):
                await self.navigate_to_test_search()
            
            # Wait for search box
            search_input = await self.page.wait_for_selector("#test-centres-near-you")
            
            # Clear existing input
            await search_input.click(click_count=3)
            await self.page.keyboard.press("Backspace")
            await self.random_delay(0.5, 1.5)
            
            # Type test center name
            await self.type_like_human("#test-centres-near-you", test_center.name)
            await self.random_delay(0.5, 1.5)
            
            # Submit search
            await MousePatternGenerator.realistic_click(self.page, "#test-centres-submit")
            await self.random_delay(1, 3)
            
            # Wait for results
            await self.page.wait_for_selector(".test-centre-results")
            
            # Check if test center is in results
            result_links = await self.page.query_selector_all(".test-centre-results a")
            found = False
            
            for link in result_links:
                text = await link.text_content()
                if test_center.name.lower() in text.lower():
                    logger.info(f"Found test center: {test_center.name}")
                    await MousePatternGenerator.realistic_click(self.page, link)
                    await self.random_delay(1, 3)
                    found = True
                    break
            
            if not found:
                logger.warning(f"Test center '{test_center.name}' not found in search results")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error searching for test center: {str(e)}")
            await self.page.screenshot(path=f"search_error_{int(time.time())}.png")
            return False
    
    async def check_available_slots(self, test_center: TestCenter) -> List[Dict]:
        """Check for available slots at a test center"""
        logger.info(f"Checking available slots at {test_center.name}")
        available_slots = []
        
        try:
            # Search for test center
            if not await self.search_test_center(test_center):
                return []
            
            # Wait for available dates to load
            await self.page.wait_for_selector(".SlotPicker-days")
            
            # Extract available dates
            date_elements = await self.page.query_selector_all(".SlotPicker-days .SlotPicker-day:not(.is-disabled)")
            
            for date_element in date_elements:
                date_str = await date_element.get_attribute("data-date")
                if not date_str:
                    continue
                
                # Parse the date
                try:
                    slot_date = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    logger.warning(f"Could not parse date: {date_str}")
                    continue
                
                # Check if the date is within our desired range
                if (slot_date < self.config.date_range.start_date or 
                    slot_date > self.config.date_range.end_date):
                    continue
                
                # Check if it's a preferred day of the week
                day_of_week = slot_date.weekday()  # 0 = Monday, 6 = Sunday
                if day_of_week not in self.config.preferred_days:
                    continue
                
                # Click on the date to see available times
                await MousePatternGenerator.realistic_click(self.page, f".SlotPicker-day[data-date='{date_str}']")
                await self.random_delay(1, 2)
                
                # Wait for time slots to load
                try:
                    await self.page.wait_for_selector(".SlotPicker-timeSlots", timeout=5000)
                except:
                    logger.warning(f"No time slots available for date {date_str}")
                    continue
                
                # Extract available times
                time_elements = await self.page.query_selector_all(".SlotPicker-timeSlots .SlotPicker-time")
                
                for time_element in time_elements:
                    time_str = await time_element.get_attribute("data-time")
                    if not time_str:
                        continue
                    
                    # Check if the time is within preferred range
                    hour, minute = map(int, time_str.split(":"))
                    earliest_hour, earliest_minute = map(int, self.config.time_preference.earliest.split(":"))
                    latest_hour, latest_minute = map(int, self.config.time_preference.latest.split(":"))
                    
                    time_value = hour * 60 + minute
                    earliest_value = earliest_hour * 60 + earliest_minute
                    latest_value = latest_hour * 60 + latest_minute
                    
                    if earliest_value <= time_value <= latest_value:
                        # This is a suitable slot
                        available_slots.append({
                            "test_center": test_center.name,
                            "date": date_str,
                            "time": time_str,
                            "element_selector": f".SlotPicker-day[data-date='{date_str}'] + .SlotPicker-day-panel .SlotPicker-time[data-time='{time_str}']"
                        })
            
            if available_slots:
                logger.info(f"Found {len(available_slots)} available slots at {test_center.name}")
            else:
                logger.info(f"No suitable slots found at {test_center.name}")
            
            return available_slots
            
        except Exception as e:
            logger.error(f"Error checking available slots: {str(e)}")
            await self.page.screenshot(path=f"slots_error_{int(time.time())}.png")
            return []
    
    async def book_slot(self, slot: Dict) -> bool:
        """Book a specific slot"""
        logger.info(f"Attempting to book slot at {slot['test_center']} on {slot['date']} at {slot['time']}")
        
        try:
            # Click on the time slot
            await MousePatternGenerator.realistic_click(self.page, slot["element_selector"])
            await self.random_delay(1, 2)
            
            # Click continue
            await MousePatternGenerator.realistic_click(self.page, ".SlotPicker-next")
            await self.random_delay(2, 4)
            
            # Confirm booking
            await MousePatternGenerator.realistic_click(self.page, "#confirm-changes")
            
            # Wait for confirmation page
            try:
                await self.page.wait_for_selector(".confirmation-block", timeout=10000)
                
                # Verify the booking was successful
                confirmation_text = await self.page.text_content(".confirmation-block")
                if "confirmed" in confirmation_text.lower() or "booked" in confirmation_text.lower():
                    logger.info(f"Successfully booked slot at {slot['test_center']} on {slot['date']} at {slot['time']}")
                    
                    # Send notifications
                    await self.notifier.notify_success(
                        test_center=slot['test_center'],
                        test_date=slot['date'],
                        test_time=slot['time']
                    )
                    
                    # Take screenshot of confirmation
                    await self.page.screenshot(path=f"booking_confirmation_{int(time.time())}.png")
                    return True
                else:
                    logger.warning("Booking attempt did not result in clear confirmation")
                    return False
                
            except Exception as e:
                logger.error(f"Error waiting for booking confirmation: {str(e)}")
                return False
            
        except Exception as e:
            logger.error(f"Error attempting to book slot: {str(e)}")
            await self.page.screenshot(path=f"booking_error_{int(time.time())}.png")
            return False
    
    async def run_monitoring_cycle(self):
        """Run a full monitoring cycle, checking all test centers"""
        logger.info("Starting monitoring cycle...")
        
        try:
            # Make sure we're logged in
            if not self.logged_in:
                success = await self.login()
                if not success:
                    logger.error("Failed to login, will retry in next cycle")
                    return False
            
            # Check each test center
            for test_center in self.config.test_centers:
                # Check if we need to rotate proxy
                self.proxy_rotator.rotate_if_needed()
                
                # Check for available slots
                available_slots = await self.check_available_slots(test_center)
                
                if available_slots:
                    # Sort slots by date and time
                    available_slots.sort(key=lambda x: (x['date'], x['time']))
                    
                    # Attempt to book the earliest slot
                    earliest_slot = available_slots[0]
                    success = await self.book_slot(earliest_slot)
                    
                    if success:
                        logger.info("Booking successful! Monitoring complete.")
                        return True
                
                # Add some delay between checking different test centers
                await self.random_delay(3, 8)
            
            logger.info("Monitoring cycle complete, no suitable slots found")
            return False
            
        except Exception as e:
            logger.error(f"Error in monitoring cycle: {str(e)}")
            return False
    
    async def run(self):
        """Main method to run the monitoring process"""
        logger.info("Starting UK driving test booking automation...")
        
        try:
            # Set up browser
            await self.setup_browser()
            
            while True:
                try:
                    # Run a monitoring cycle
                    success = await self.run_monitoring_cycle()
                    if success:
                        logger.info("Booking successful, stopping automation")
                        break
                    
                except Exception as e:
                    logger.error(f"Error in monitoring cycle: {str(e)}")
                    # Restart session on error
                    await self.restart_session()
                
                # Wait before next cycle with random delay
                wait_time = random.randint(
                    self.config.check_interval[0],
                    self.config.check_interval[1]
                )
                logger.info(f"Waiting {wait_time} seconds before next check...")
                await asyncio.sleep(wait_time)
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        
        finally:
            # Clean up resources
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            
            logger.info("Automation completed")


async def main():
    """Run the driving test booking automation"""
    parser = argparse.ArgumentParser(description="UK Driving Test Booking Automation")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    args = parser.parse_args()
    
    booker = DrivingTestBooker(args.config)
    await booker.run()


if __name__ == "__main__":
    asyncio.run(main())
