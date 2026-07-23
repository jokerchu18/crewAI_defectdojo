import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from defectdojo_crewai.tools import defectdojo_api


class _Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"results": []}


class DefectDojoToolConcurrencyTests(unittest.TestCase):
    def test_tool_calls_respect_semaphore_limit(self) -> None:
        original_semaphore = defectdojo_api._DEFECTDOJO_TOOL_SEMAPHORE
        defectdojo_api._DEFECTDOJO_TOOL_SEMAPHORE = threading.BoundedSemaphore(2)
        active = 0
        max_active = 0
        counter_lock = threading.Lock()

        def fake_get(*args, **kwargs):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.05)
                return _Response()
            finally:
                with counter_lock:
                    active -= 1

        try:
            with patch.object(defectdojo_api.httpx, "get", side_effect=fake_get):
                with ThreadPoolExecutor(max_workers=6) as pool:
                    futures = [
                        pool.submit(
                            defectdojo_api.defectdojo_get_finding_tool,
                            "http://defectdojo.test",
                            "token",
                            finding_id,
                        )
                        for finding_id in range(6)
                    ]
                    for future in futures:
                        self.assertEqual(future.result(), {"results": []})
        finally:
            defectdojo_api._DEFECTDOJO_TOOL_SEMAPHORE = original_semaphore

        self.assertEqual(max_active, 2)


if __name__ == "__main__":
    unittest.main()
