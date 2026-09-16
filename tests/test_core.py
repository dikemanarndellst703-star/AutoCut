import unittest
from core import build_timeline, classify, export_ranges, piece, subtitles, validate_segments


class TimelineTests(unittest.TestCase):
    def test_covers_gaps_and_tail(self):
        data = [{'start': 12, 'end': 17, 'text': '跟我慢慢吸气'}, {'start': 62, 'end': 68, 'text': '因为这有助于放松'}]
        timeline = build_timeline(data, 105)
        self.assertEqual(timeline[0]['start'], 0)
        self.assertEqual(timeline[-1]['end'], 105)
        self.assertTrue(all(a['end'] == b['start'] for a, b in zip(timeline, timeline[1:])))
        self.assertTrue(all(s['keep'] for s in timeline))
        self.assertTrue(any(not s['text'] and s['category'] == 'do' for s in timeline))

    def test_empty_and_overlapping_transcription(self):
        blank = build_timeline([], 65.25)
        self.assertEqual([s['end'] for s in blank], [30, 60, 65.25])
        rows = build_timeline([{'start': 0, 'end': 5, 'text': '吸气'}, {'start': 4, 'end': 8, 'text': '呼气'}], 10)
        self.assertEqual(rows[1]['start'], 5)

    def test_explanation_is_not_practice(self):
        self.assertEqual(classify('为什么我们需要慢慢吸气？因为这有助于放松', 20, 100)[0], 'know')
        self.assertEqual(classify('跟我慢慢吸气，一、二、三、四', 20, 100)[0], 'do')
        self.assertEqual(classify('感谢观看，下期再见', 95, 100)[0], 'other')

    def test_analysis_excludes_other_but_keeps_content_and_gaps(self):
        rows = build_timeline([{'start':0,'end':2,'text':'欢迎来到'}, {'start':2,'end':5,'text':'因为这有助于放松'}, {'start':5,'end':8,'text':'跟我吸气'}], 10)
        self.assertFalse(rows[0]['keep'])
        self.assertTrue(all(s['keep'] for s in rows[1:]))
        self.assertEqual(export_ranges(rows)[1], [[2, 10]])

    def test_reject_invalid_timeline(self):
        for data in [[piece(1, 10)], [piece(0, 5), piece(4, 10)], [piece(0, 5)], [piece(0, float('nan'))]]:
            with self.assertRaises(ValueError):
                validate_segments(data, 10)

    def test_gaps_inherit_previous_category_and_always_default_keep(self):
        rows=build_timeline([
            {'start':1,'end':2,'text':'欢迎来到'},
            {'start':4,'end':5,'text':'因为这有助于放松'},
            {'start':7,'end':8,'text':'跟我吸气'},
        ],10)
        gaps=[s for s in rows if not s['text']]
        self.assertEqual([s['category'] for s in gaps],['know','other','know','do'])
        self.assertTrue(all(s['keep'] for s in gaps))
        self.assertFalse(rows[1]['keep'])

    def test_export_keeps_order_and_remaps_subtitles(self):
        rows = [piece(0, 2, '片头', 'other'), piece(2, 5, '讲解', 'know'), piece(5, 7, '', 'do'), piece(7, 10, '跟练', 'do')]
        rows[0]['keep'] = False
        selected, ranges = export_ranges(rows, 'do')
        self.assertEqual(ranges, [[5, 10]])
        self.assertIn('00:00:02,000 --> 00:00:05,000', subtitles(selected, True))
        self.assertIn('00:00:07,000 --> 00:00:10,000', subtitles(selected, False))
        self.assertEqual(export_ranges(rows)[1], [[2, 10]])


if __name__ == '__main__':
    unittest.main()
