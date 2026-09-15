import unittest

from dedup import extract_pairs, filter_new_rows, normalize_email, normalize_social_url


class DedupTests(unittest.TestCase):
    def test_url_variants(self):
        for value in (
            'http://www.instagram.com/Creator/?utm_source=test#bio',
            'instagram.com/creator', '//www.instagram.com/CREATOR/',
            'https://instagram.com/%63reator/',
        ):
            self.assertEqual(normalize_social_url(value), 'https://instagram.com/creator')

    def test_aliases(self):
        self.assertEqual(normalize_social_url('https://twitter.com/Creator'),
                         normalize_social_url('https://x.com/creator'))

    def test_distinct_identifiers_survive(self):
        self.assertNotEqual(normalize_social_url('https://youtube.com/channel/AbC'),
                            normalize_social_url('https://youtube.com/channel/abc'))
        self.assertNotEqual(normalize_social_url('https://facebook.com/profile.php?id=1'),
                            normalize_social_url('https://facebook.com/profile.php?id=2'))

    def test_invalid_urls(self):
        for value in ('', 'javascript:alert(1)', 'https://instagram.com.evil.test/a',
                      'https://user:pass@instagram.com/a', 'https://instagram.com:999/a'):
            self.assertEqual(normalize_social_url(value), '')

    def test_email(self):
        self.assertEqual(normalize_email(' MAILTO:User@Example.COM?subject=test '), 'user@example.com')

    def test_new_contact_for_existing_profile(self):
        existing = extract_pairs([['Old', 'https://www.instagram.com/CREATOR/', 'old@example.com']])
        rows = filter_new_rows([
            ['New', 'http://instagram.com/creator', 'OLD@example.com'],
            ['New', 'https://instagram.com/creator', 'new@example.com'],
            ['New', 'https://instagram.com/creator', 'NEW@example.com'],
        ], existing)
        self.assertEqual([row[2] for row in rows], ['new@example.com'])

    def test_empty_contact_not_repeated(self):
        for contact in ('', 'old@example.com'):
            existing = extract_pairs([['Old', 'https://instagram.com/creator', contact]])
            self.assertEqual(filter_new_rows([['New', 'https://instagram.com/CREATOR/', '']], existing), [])

    def test_empty_contact_can_be_enriched(self):
        existing = extract_pairs([['Name', 'https://instagram.com/creator', '']])
        self.assertEqual(len(filter_new_rows([['Name', 'https://instagram.com/creator', 'new@example.com']], existing)), 1)

    def test_same_email_different_profiles(self):
        existing = extract_pairs([['A', 'https://instagram.com/a', 'agency@example.com']])
        self.assertEqual(len(filter_new_rows([['B', 'https://instagram.com/b', 'agency@example.com']], existing)), 1)

    def test_multiple_contacts_and_urls(self):
        pairs = extract_pairs([['https://instagram.com/a', 'a@example.com; b@example.com',
                                '', '', 'https://instagram.com/b', 'c@example.com']])
        self.assertIn(('https://instagram.com/a', 'b@example.com'), pairs)
        self.assertIn(('https://instagram.com/b', 'c@example.com'), pairs)
        self.assertNotIn(('https://instagram.com/b', 'a@example.com'), pairs)

    def test_ambiguous_layout_fails_closed(self):
        with self.assertRaises(ValueError):
            extract_pairs([['https://instagram.com/a', 'a@example.com', 'https://instagram.com/b']])

    def test_no_cross_row_pairs(self):
        pairs = extract_pairs([['https://instagram.com/a'], ['a@example.com']])
        self.assertNotIn(('https://instagram.com/a', 'a@example.com'), pairs)

    def test_blank_and_multiple_urls_do_not_crash(self):
        self.assertEqual(extract_pairs([]), set())
        pairs = extract_pairs([['A', 'https://instagram.com/a', 'a@example.com',
                                'B', 'https://instagram.com/b', 'b@example.com',
                                'C', 'https://instagram.com/c', '']])
        self.assertIn(('https://instagram.com/a', 'a@example.com'), pairs)
        self.assertIn(('https://instagram.com/b', 'b@example.com'), pairs)
        self.assertIn(('https://instagram.com/c', ''), pairs)


if __name__ == '__main__':
    unittest.main()
