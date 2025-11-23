"""
Tests for common.visualization module
"""
import pytest
import os
from PIL import Image
from common.visualization import (
    generate_48h_schedule_image,
    generate_24h_schedule_image
)


@pytest.mark.unit
@pytest.mark.visualization
class TestVisualization:
    """Tests for schedule image generation"""
    
    @pytest.fixture
    def font_path(self):
        """Path to a font file for testing"""
        # Try to find the font in resources
        resource_font = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../resources/DejaVuSans.ttf'))
        if os.path.exists(resource_font):
            return resource_font
        
        # Fallback to system font or skip if not found
        return "arial.ttf" 

    def test_generate_48h_schedule_image(self, font_path, tmp_path):
        """Test generating 48h schedule image"""
        schedule_data = {
            "12.11.24": [
                {"shutdown": "10:00–12:00"},
                {"shutdown": "18:00–20:00"}
            ],
            "13.11.24": [
                {"shutdown": "08:00–12:00"}
            ]
        }
        
        try:
            image_bytes = generate_48h_schedule_image(schedule_data, font_path=font_path)
            
            assert isinstance(image_bytes, bytes)
            assert len(image_bytes) > 0
            
            # Verify it's a valid image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            assert img.format == "PNG"
            assert img.width == 300
            assert img.height == 300
            
        except OSError:
            pytest.skip("Font not found, skipping visualization test")

    def test_generate_24h_schedule_image(self, font_path):
        """Test generating 24h schedule image"""
        schedule_data = {
            "12.11.24": [
                {"shutdown": "10:00–12:00"},
                {"shutdown": "18:00–20:00"}
            ]
        }
        
        try:
            image_bytes = generate_24h_schedule_image(schedule_data, font_path=font_path)
            
            assert isinstance(image_bytes, bytes)
            assert len(image_bytes) > 0
            
            # Verify it's a valid image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            assert img.format == "PNG"
            assert img.width == 300
            assert img.height == 300
            
        except OSError:
            pytest.skip("Font not found, skipping visualization test")

    def test_generate_48h_image_empty_schedule(self, font_path):
        """Test 48h image with empty schedule"""
        schedule_data = {
            "12.11.24": [],
            "13.11.24": []
        }
        
        try:
            image_bytes = generate_48h_schedule_image(schedule_data, font_path=font_path)
            # Should return None if no shutdowns
            assert image_bytes is None
        except OSError:
            pytest.skip("Font not found")

    def test_generate_24h_image_empty_schedule(self, font_path):
        """Test 24h image with empty schedule"""
        schedule_data = {
            "12.11.24": []
        }
        
        try:
            image_bytes = generate_24h_schedule_image(schedule_data, font_path=font_path)
            # Should return None if no shutdowns (based on implementation logic check)
            # Implementation says: if not has_any_shutdowns: return None
            # Wait, for 24h: hours_status = [False]*24. If no slots, all False.
            # Does it return None?
            # Code: if not today_slots: return None
            assert image_bytes is None
        except OSError:
            pytest.skip("Font not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
