"""
Security middleware for FastAPI to protect against vulnerability scanners and malicious requests.

Features:
- Rate limiting (requests per minute per IP)
- Suspicious path detection and blocking
- IP-based temporary blocking
- Whitelist for trusted IPs
"""

import time
import logging
import ipaddress
from typing import Dict, Set, Tuple
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastapi import status
from datetime import datetime
import pytz

# Настройка логирования с Kyiv timezone
def custom_time(*args):
    """Возвращает текущее время в Киевском часовом поясе для логирования."""
    return datetime.now(pytz.timezone('Europe/Kyiv')).timetuple()

logger = logging.getLogger(__name__)
# Применяем custom time converter для этого logger
for handler in logging.root.handlers:
    if handler.formatter:
        handler.formatter.converter = custom_time

# Security configuration
RATE_LIMIT_REQUESTS = 60  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds
BLOCK_DURATION = 900  # 15 minutes in seconds
MAX_FAILED_REQUESTS = 10  # before blocking IP

# Suspicious paths that indicate scanner activity
SUSPICIOUS_PATHS = [
    '/admin', '/login', '/manage', '/cgi-bin',
    '/wp-admin', '/phpmyadmin', '/mysql',
    '.php', '.asp', '.aspx', '.jsp',
    '/console', '/api/v1/login', '/user/login',
    '/%2B', '%20', '%2F',  # URL-encoded attempts
]

# Whitelisted IP ranges (private networks, localhost)
WHITELISTED_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),      # localhost
    ipaddress.ip_network('10.0.0.0/8'),       # private
    ipaddress.ip_network('172.16.0.0/12'),    # Docker default
    ipaddress.ip_network('192.168.0.0/16'),   # private
]


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware to protect API from scanners and malicious requests.
    """
    
    def __init__(self, app):
        super().__init__(app)
        # IP -> [(timestamp, was_blocked)]
        self.request_history: Dict[str, list] = defaultdict(list)
        # IP -> block_until_timestamp
        self.blocked_ips: Dict[str, float] = {}
        # IP -> failed_request_count
        self.failed_requests: Dict[str, int] = defaultdict(int)
        
        logger.info("Security middleware initialized")
    
    def _is_whitelisted(self, ip: str) -> bool:
        """Check if IP is in whitelist."""
        try:
            ip_obj = ipaddress.ip_address(ip)
            return any(ip_obj in network for network in WHITELISTED_NETWORKS)
        except ValueError:
            return False
    
    def _is_blocked(self, ip: str) -> bool:
        """Check if IP is currently blocked."""
        if ip in self.blocked_ips:
            if time.time() < self.blocked_ips[ip]:
                return True
            else:
                # Block expired, remove it
                del self.blocked_ips[ip]
                self.failed_requests[ip] = 0
        return False
    
    def _block_ip(self, ip: str, reason: str):
        """Block IP for BLOCK_DURATION seconds."""
        block_until = time.time() + BLOCK_DURATION
        self.blocked_ips[ip] = block_until
        logger.warning(f"🚫 Blocked IP {ip} for {BLOCK_DURATION}s. Reason: {reason}")
    
    def _check_rate_limit(self, ip: str) -> bool:
        """
        Check if IP exceeds rate limit.
        Returns True if rate limit exceeded.
        """
        now = time.time()
        
        # Clean old entries
        self.request_history[ip] = [
            ts for ts in self.request_history[ip]
            if now - ts < RATE_LIMIT_WINDOW
        ]
        
        # Check rate
        if len(self.request_history[ip]) >= RATE_LIMIT_REQUESTS:
            return True
        
        # Add current request
        self.request_history[ip].append(now)
        return False
    
    def _is_suspicious_path(self, path: str) -> bool:
        """Check if path matches suspicious patterns."""
        path_lower = path.lower()
        return any(pattern in path_lower for pattern in SUSPICIOUS_PATHS)
    
    def _increment_failed_requests(self, ip: str):
        """Increment failed request counter and block if threshold exceeded."""
        self.failed_requests[ip] += 1
        if self.failed_requests[ip] >= MAX_FAILED_REQUESTS:
            self._block_ip(ip, f"Too many failed requests ({self.failed_requests[ip]})")
    
    async def dispatch(self, request: Request, call_next):
        """Process request through security checks."""
        
        # Get client IP
        client_ip = request.client.host
        path = request.url.path
        
        # Skip security for health check
        if path == "/health":
            return await call_next(request)
        
        # Whitelist check
        if self._is_whitelisted(client_ip):
            return await call_next(request)
        
        # Check if IP is blocked
        if self._is_blocked(client_ip):
            logger.warning(f"⛔ Blocked request from {client_ip} to {path}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access forbidden"}
            )
        
        # Check for suspicious paths
        if self._is_suspicious_path(path):
            logger.warning(f"🔍 Suspicious path detected: {client_ip} -> {path}")
            self._increment_failed_requests(client_ip)
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Not found"}
            )
        
        # Rate limiting check
        if self._check_rate_limit(client_ip):
            logger.warning(f"⚡ Rate limit exceeded for {client_ip}")
            self._block_ip(client_ip, "Rate limit exceeded")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests"}
            )
        
        # Process request
        try:
            response = await call_next(request)
            
            # Track 404s as potential scanning
            if response.status_code == 404:
                self._increment_failed_requests(client_ip)
                logger.info(f"404 from {client_ip} to {path} (failed count: {self.failed_requests[client_ip]})")
            
            return response
            
        except Exception as e:
            logger.error(f"Error processing request from {client_ip}: {e}")
            raise
