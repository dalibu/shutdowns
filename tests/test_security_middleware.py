"""
Tests for security middleware.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from security_middleware import SecurityMiddleware


@pytest.fixture
def app():
    """Create a test FastAPI app with security middleware."""
    test_app = FastAPI()
    test_app.add_middleware(SecurityMiddleware)
    
    @test_app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @test_app.get("/test")
    async def test_endpoint():
        return {"message": "success"}
    
    @test_app.get("/admin")
    async def admin_endpoint():
        return {"message": "admin"}
    
    return test_app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestSecurityMiddleware:
    """Test security middleware functionality."""
    
    def test_health_check_bypasses_security(self, client):
        """Health check endpoint should bypass all security checks."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    
    def test_legitimate_request_passes(self, client):
        """Legitimate requests should pass through."""
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"message": "success"}
    
    def test_suspicious_path_blocked(self, client):
        """Suspicious paths should be blocked."""
        suspicious_paths = [
            "/admin",
            "/login",
            "/wp-admin/index.php",
            "/cgi-bin/login.cgi",
            "/phpmyadmin",
        ]
        
        for path in suspicious_paths:
            response = client.get(path)
            assert response.status_code == 404, f"Path {path} should be blocked"
            assert "Not found" in response.json()["detail"]
    
    def test_rate_limiting(self, client):
        """Rate limiting should block excessive requests."""
        # Make 61 requests (limit is 60)
        for i in range(61):
            response = client.get("/test")
            if i < 60:
                assert response.status_code == 200, f"Request {i} should pass"
            else:
                # 61st request should be rate limited
                assert response.status_code == 429, "Should be rate limited"
                assert "Too many requests" in response.json()["detail"]
    
    def test_failed_requests_blocking(self, client):
        """Multiple failed requests should block IP."""
        # Make 11 requests to suspicious paths (limit is 10)
        for i in range(11):
            response = client.get("/admin/test")
            if i < 10:
                assert response.status_code == 404
            else:
                # After 10 failed requests, IP should be blocked
                assert response.status_code == 403
                assert "Access forbidden" in response.json()["detail"]
    
    def test_blocked_ip_stays_blocked(self, client):
        """Once blocked, IP should stay blocked for subsequent requests."""
        # Trigger blocking by making 11 suspicious requests
        for _ in range(11):
            client.get("/admin/test")
        
        # Try a legitimate request - should still be blocked
        response = client.get("/test")
        assert response.status_code == 403
        assert "Access forbidden" in response.json()["detail"]
    
    def test_url_encoded_suspicious_paths(self, client):
        """URL-encoded suspicious paths should be detected."""
        response = client.get("/%2BCSCOE%2B/logon.html")
        assert response.status_code == 404


class TestSecurityLogging:
    """Test security event logging."""
    
    def test_suspicious_request_logged(self, client, caplog):
        """Suspicious requests should be logged."""
        import logging
        with caplog.at_level(logging.WARNING):
            client.get("/admin/index.html")
            assert "Suspicious path detected" in caplog.text
    
    def test_rate_limit_logged(self, client, caplog):
        """Rate limit violations should be logged."""
        import logging
        with caplog.at_level(logging.WARNING):
            # Trigger rate limit
            for _ in range(61):
                client.get("/test")
            assert "Rate limit exceeded" in caplog.text
    
    def test_blocking_logged(self, client, caplog):
        """IP blocking should be logged."""
        import logging
        with caplog.at_level(logging.WARNING):
            # Trigger blocking
            for _ in range(11):
                client.get("/admin/test")
            assert "Blocked IP" in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
