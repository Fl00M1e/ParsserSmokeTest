import csv
import io
import unittest
from unittest.mock import Mock, patch

from sheets import GoogleSheetsWriter, SheetsSafetyError


class SheetsTests(unittest.TestCase):
    def setUp(self):
        self.matrix = []
        self.clipboard = 'user clipboard'
        self.pastes = 0
        self.writer = GoogleSheetsWriter('test-id', 'Sheet1', log=lambda _: None)
        self.writer.connected = True
        self.writer.sheet_page = Mock()
        self.writer._read_active_sheet_matrix_via_export = Mock(side_effect=lambda _: [list(r) for r in self.matrix])
        self.writer._goto_cell = Mock()
        self.writer.sheet_page.keyboard.press.side_effect = self.paste
        self.clip_patch = patch('sheets.pyperclip.copy', side_effect=self.copy)
        self.read_patch = patch('sheets.pyperclip.paste', side_effect=lambda: self.clipboard)
        self.clip_patch.start()
        self.read_patch.start()
        self.addCleanup(self.clip_patch.stop)
        self.addCleanup(self.read_patch.stop)

    def copy(self, value):
        self.clipboard = value

    def paste(self, key):
        self.pastes += 1
        cell = self.writer._goto_cell.call_args.args[0]
        column, row = self.writer._parse_cell(cell)
        rows = list(csv.reader(io.StringIO(self.clipboard), delimiter='\t'))
        while len(self.matrix) < row - 1 + len(rows):
            self.matrix.append([])
        for i, values in enumerate(rows, row - 1):
            self.matrix[i] += [''] * max(0, column + 2 - len(self.matrix[i]))
            self.matrix[i][column - 1:column + 2] = [v[1:] if v.startswith("'") else v for v in values]

    def test_repeat_runs_and_occupied_rows(self):
        row = ['Name', 'https://instagram.com/creator', 'a@example.com']
        self.assertEqual(self.writer.append_rows([row]), [row])
        self.writer.set_start_cell('A2')
        self.assertEqual(self.writer.append_rows([row]), [])
        other = ['Other', 'https://instagram.com/other', 'b@example.com']
        self.writer.append_rows([other])
        self.assertEqual(self.matrix[1:3], [row, other])
        self.assertEqual(self.pastes, 2)
        self.assertEqual(self.clipboard, 'user clipboard')

    def test_external_row_added_after_preload(self):
        self.matrix = [['Manual', 'https://www.instagram.com/CREATOR/', 'A@example.com']]
        self.assertEqual(self.writer.append_rows([['Name', 'http://instagram.com/creator', 'a@example.com']]), [])
        self.assertEqual(self.pastes, 0)

    def test_blank_contact_repeated(self):
        row = ['Name', 'https://instagram.com/creator', '']
        self.writer.append_rows([row])
        self.writer.set_start_cell('A2')
        self.assertEqual(self.writer.append_rows([row]), [])

    def test_failed_read_does_not_paste(self):
        self.writer._read_active_sheet_matrix_via_export.side_effect = RuntimeError('offline')
        with self.assertRaises(SheetsSafetyError):
            self.writer.append_rows([['Name', 'https://instagram.com/creator', 'a@example.com']])
        self.assertEqual(self.pastes, 0)
        self.assertTrue(self.writer.write_blocked)

    def test_unconfirmed_paste_not_retried(self):
        self.writer.sheet_page.keyboard.press.side_effect = lambda _: None
        with self.assertRaises(SheetsSafetyError):
            self.writer.append_rows([['Name', 'https://instagram.com/creator', 'a@example.com']])
        self.assertEqual(self.writer.sheet_page.keyboard.press.call_count, 1)
        self.assertEqual(self.writer.current_row, 2)
        self.assertEqual(self.writer.verified_pairs, set())
        with self.assertRaises(SheetsSafetyError):
            self.writer.append_rows([['Name', 'https://instagram.com/creator', 'a@example.com']])
        self.assertEqual(self.writer.sheet_page.keyboard.press.call_count, 1)

    def test_formula_name_and_custom_column(self):
        self.writer.set_start_cell('D10')
        row = ['=1+1', 'https://instagram.com/creator', 'a@example.com']
        self.writer.append_rows([row])
        self.assertEqual(self.matrix[9][3:6], row)

    def test_literal_apostrophe_name(self):
        row = ["'Creator", 'https://instagram.com/creator', 'a@example.com']
        self.assertEqual(self.writer.append_rows([row]), [row])

    def test_existing_contact_does_not_hide_new_contact(self):
        self.matrix = [['Name', 'https://instagram.com/creator', 'old@example.com']]
        new = ['Name', 'https://instagram.com/creator', 'new@example.com']
        self.assertEqual(self.writer.append_rows([self.matrix[0], new, new]), [new])
        self.assertEqual(self.pastes, 1)

    def test_user_clipboard_change_is_preserved(self):
        original_paste = self.paste
        def paste_and_copy(key):
            original_paste(key)
            self.clipboard = 'copied by user'
        self.writer.sheet_page.keyboard.press.side_effect = paste_and_copy
        self.writer.append_rows([['Name', 'https://instagram.com/creator', 'a@example.com']])
        self.assertEqual(self.clipboard, 'copied by user')


class CsvSnapshotTests(unittest.TestCase):
    def make_writer(self, media_type, body, status=200):
        context = Mock()
        response = context.request.get.return_value
        response.status = status
        response.headers = {'content-type': media_type}
        response.text.return_value = body
        writer = GoogleSheetsWriter('test-id', 'Sheet1', context)
        writer.connected = True
        writer.sheet_page = Mock(url='https://docs.google.com/spreadsheets/d/test-id/edit#gid=1')
        writer._activate_sheet_tab_by_name = Mock()
        writer._get_sheet_gid_by_name = Mock(return_value='1')
        return writer, response

    def test_empty_csv_is_success(self):
        writer, response = self.make_writer('text/csv; charset=utf-8', '')
        self.assertEqual(writer._read_active_sheet_matrix_via_export('Sheet1'), [])
        response.dispose.assert_called_once()

    def test_html_or_error_rejected(self):
        for media, body, status in [('text/html', '<html>login</html>', 200),
                                     ('text/csv', '<html>login</html>', 200),
                                     ('text/csv', 'error', 403)]:
            writer, response = self.make_writer(media, body, status)
            with self.assertRaises(SheetsSafetyError):
                writer._read_active_sheet_matrix_via_export('Sheet1')
            response.dispose.assert_called_once()

    def test_multiline_csv(self):
        writer, _ = self.make_writer('text/csv', '"a\nb",c,d\n')
        self.assertEqual(writer._read_active_sheet_matrix_via_export('Sheet1'), [['a\nb', 'c', 'd']])

    def test_other_document_rejected(self):
        writer, _ = self.make_writer('text/csv', '')
        writer.sheet_page.url = 'https://docs.google.com/spreadsheets/d/other/edit'
        with self.assertRaises(SheetsSafetyError):
            writer._read_active_sheet_matrix_via_export('Sheet1')
        writer.browser_context.request.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
