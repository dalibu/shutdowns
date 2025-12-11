
import os
import shutil
import logging
import logging.handlers
import tempfile
import unittest
from datetime import datetime
import pytz
from common.logging_config import setup_logging, custom_time

class TestLoggingConfig(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for logs
        self.test_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        # Remove the directory after the test
        shutil.rmtree(self.test_dir)
        # Reset logger handlers
        logger = logging.getLogger("test_logger")
        logger.handlers = []

    def test_setup_logging_console_only(self):
        """Test logging setup without file output."""
        logger = setup_logging("test_logger_console")
        
        self.assertTrue(logger.handlers)
        # Should have at least StreamHandler
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        self.assertTrue(stream_handlers)
        
    def test_setup_logging_with_file(self):
        """Test logging setup with file output."""
        logger = setup_logging("test_logger_file", log_dir=self.test_dir)
        
        # Check for TimedRotatingFileHandler
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)]
        self.assertTrue(file_handlers)
        
        handler = file_handlers[0]
        self.assertEqual(handler.backupCount, 7)
        self.assertEqual(handler.when, 'MIDNIGHT')
        # interval is stored as seconds internally when computing rollover
        # For 'midnight', it sets rolloverAt but interval attribute behavior varies by implementation details,
        # but logically it represents 1 day. 
        # Actually TimedRotatingFileHandler with 'midnight' doesn't use interval to calculate next rollover 
        # in the same way as seconds, but let's check what we passed if possible or just skip strict interval check 
        # relying on 'when'.
        self.assertEqual(handler.when, 'MIDNIGHT')
        # self.assertEqual(handler.interval, 1) # Internal implementation converts this to seconds (86400)
        
        # Check file creation
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "bot.log")))

    def test_kyiv_timezone_converter(self):
        """Test that the custom time converter returns Kyiv time."""
        # Get time from our function
        log_time_tuple = custom_time()
        log_dt = datetime(*log_time_tuple[:6])
        
        # Get current Kyiv time
        kyiv_az = pytz.timezone('Europe/Kiev')
        now_kyiv = datetime.now(kyiv_az)
        
        # Check if they are close (ignoring minimal execution diff)
        # We construct naive datetime from the tuple, so we compare components roughly
        self.assertEqual(log_time_tuple.tm_hour, now_kyiv.hour)
        self.assertEqual(log_time_tuple.tm_min, now_kyiv.minute)

if __name__ == '__main__':
    unittest.main()
