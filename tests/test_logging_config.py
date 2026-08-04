import logging
import unittest

from defectdojo_crewai.utils.logging_config import (
    _BusinessLogFilter,
    _SystemLogFilter,
)


class LoggingConfigTests(unittest.TestCase):
    def test_project_loggers_are_business_logs(self) -> None:
        record = logging.LogRecord(
            "defectdojo_crewai.knowledge.kg.enricher",
            logging.INFO,
            __file__,
            1,
            "CVE graph enriched",
            (),
            None,
        )

        self.assertTrue(_BusinessLogFilter().filter(record))
        self.assertFalse(_SystemLogFilter().filter(record))

    def test_dependency_loggers_are_system_logs(self) -> None:
        record = logging.LogRecord(
            "httpx",
            logging.INFO,
            __file__,
            1,
            "HTTP request completed",
            (),
            None,
        )

        self.assertFalse(_BusinessLogFilter().filter(record))
        self.assertTrue(_SystemLogFilter().filter(record))


if __name__ == "__main__":
    unittest.main()
