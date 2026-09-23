import unittest

from gate_expanded_observer import collect_extra_observations


class ExpandedObservationTest(unittest.TestCase):
    def test_pages_extend_research_only_and_report_optional_failure(self):
        urls, parsed, sleeps, screened = [], [], [], []

        def fetch(url):
            urls.append(url)
            if "page=3" in url and "new_pools" in url:
                return {"_avci_data_error": True, "data": []}
            return {"data": [{"id": url}]}

        def parse(payload, network_id, network_name, source, observation=False):
            self.assertTrue(observation)
            parsed.append((network_id, source))
            return payload["data"]

        rows, errors, count = collect_extra_observations(
            {"solana": "Solana"}, fetch, parse, sleeps.append,
            on_payload=lambda payload, network, name, source:
                screened.append((network, source, payload["data"][0]["id"]))
        )
        self.assertEqual(count, 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            errors, ["solana:new_pools:page3"]
        )
        self.assertEqual(len(urls), 4)
        self.assertEqual(len(screened), 3)
        self.assertTrue(all("page=" in row[2] for row in screened))
        self.assertEqual(sleeps, [7, 7, 7])
        self.assertEqual(parsed, [
            ("solana", "TRENDING"), ("solana", "TRENDING"),
            ("solana", "NEW")
        ])


if __name__ == "__main__":
    unittest.main()
