"""Exercise real DOM selectors and report navigation in isolated, headless Chrome.

Run explicitly: python -m unittest discover -s tests -p test_parser_browser.py -v
All page requests are fulfilled locally; no ON Social account is used.
"""
import unittest

from playwright.sync_api import sync_playwright

from parser import OnSocialParser, StopRequested


class AnalyzeBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(
            content_type='text/html', body='<h1>Local report fixture</h1>'))
        self.page = self.context.new_page()
        self.page.goto('https://app.onsocial.ai/influencer-identification')
        self.parser = OnSocialParser(self.context, log=lambda _: None)
        self.addCleanup(self.context.close)

    def test_green_gray_disabled_hidden_and_duplicate_links(self):
        self.page.set_content('''
            <div data-profile-id="green"><button style="background:green">Analyze</button></div>
            <div data-profile-id="gray"><button style="background:gray">Analyzed</button></div>
            <a href="/audience-data/1?url=https%3A%2F%2Finstagram.com%2Fcreator">
                <button disabled>Analyze</button></a>
            <a href="/audience-data/1?url=https%3A%2F%2Fwww.instagram.com%2FCREATOR%2F">View report</a>
            <div data-profile-id="blocked"><button disabled>Analyze</button></div>
            <div data-profile-id="hidden" hidden><button>Analyze</button></div>
            <button>Unlock next 10</button>
        ''')
        elements = self.parser.find_analyze_buttons(self.page)
        self.assertEqual(len(elements), 3)
        self.parser.mark_processed_element(elements[1])
        self.assertEqual(len(self.parser.find_analyze_buttons(self.page)), 2)
        # A new run can open the already analyzed report again.
        self.assertEqual(len(OnSocialParser(self.context).find_analyze_buttons(self.page)), 3)

    def test_nested_plain_buttons_keep_separate_stable_card_keys(self):
        self.page.set_content('''
            <section><h2>Creator A</h2><div><button>Analyze</button></div></section>
            <section><h2>Creator B</h2><div><button>Analyze</button></div></section>
        ''')
        elements = self.parser.find_analyze_buttons(self.page)
        self.assertEqual(len(elements), 2)
        self.parser.mark_processed_element(elements[0])
        elements[0].evaluate("el => el.textContent = 'Analyzed'")
        self.assertEqual(len(self.parser.find_analyze_buttons(self.page)), 1)

    def test_report_with_disabled_child_opens_and_is_not_revisited(self):
        self.page.set_content('''<a href="/audience-data/42?url=https://instagram.com/creator">
            <button disabled>Analyze</button></a>''')
        elements = self.parser.find_analyze_buttons(self.page)
        self.assertEqual(len(elements), 1)
        report = self.parser.click_analyze_and_get_page(self.page, elements[0])
        self.assertIn('/audience-data/42', report.url)
        self.assertEqual(report.locator('h1').inner_text(), 'Local report fixture')
        self.assertEqual(self.parser.find_analyze_buttons(self.page), [])

    def test_green_button_click_navigates(self):
        self.page.set_content('''<div data-profile-id="new"><button
            onclick="location.href='/audience-data/99'">Analyze</button></div>''')
        report = self.parser.click_analyze_and_get_page(
            self.page, self.parser.find_analyze_buttons(self.page)[0])
        self.assertEqual(report, self.page)
        self.assertTrue(report.url.endswith('/audience-data/99'))

    def test_external_or_nonreport_links_are_not_used_for_disabled_controls(self):
        self.page.set_content('''
            <a href="https://other.example/audience-data/1"><button disabled>Analyze</button></a>
            <a href="/settings"><button disabled>Analyze</button></a>
        ''')
        self.assertEqual(self.parser.find_analyze_buttons(self.page), [])

    def test_stop_prevents_search_and_open(self):
        self.parser.stop_checker = lambda: True
        with self.assertRaises(StopRequested):
            self.parser.find_analyze_buttons(self.page)
        with self.assertRaises(StopRequested):
            self.parser.click_analyze_and_get_page(self.page, None)


if __name__ == '__main__':
    unittest.main()
